import asyncio
import os
import pickle
import time
from typing import Any, Dict, List, Optional, Callable
from resolvers.fuzzy_concept_resolver import FuzzyConceptResolver
from resolvers.sql_helpers import build_concepts_by_id, build_name_token_index
from logging_config import get_logger

log = get_logger("store")


class ResolverStore:
    def __init__(
        self,
        loader: Callable[[], List[Dict[str, Any]]],
        ttl_seconds: int,
        build_core: Optional[
            Callable[["ResolverStore", List[Dict[str, Any]]], None]
        ] = None,
        load_enrichment: Optional[
            Callable[["ResolverStore", List[Dict[str, Any]]], None]
        ] = None,
        cache_path: Optional[str] = None,
        fingerprint: Optional[Callable[[], Any]] = None,
    ):
        self._loader = loader
        self._ttl = ttl_seconds
        # Two-phase warm-up: build_core builds the fast matching indexes in seconds;
        # load_enrichment runs the slow serialised DB loads. Each flips its own has_loaded_* flag.
        self._build_core = build_core
        self._load_enrichment = load_enrichment
        self._lock = asyncio.Lock()
        self._refresh_task: Optional[asyncio.Task] = None

        # Per-capability flags, monotonic: set True as data lands, never reset on refresh (old
        # maps stay live), so a capability once on stays on even if a later reload fails.
        self._core_loaded = False
        self._acronyms_loaded = False
        self._synonyms_loaded = False
        self._ancestors_loaded = False

        # Cheap change-detection probe; when it matches the last load, skip the reload.
        self._fingerprint = fingerprint
        self._last_fingerprint: Any = None

        # Dev-only warm-up snapshot cache. When set, the fully warmed state is pickled to
        # this path so uvicorn --reload restores it instead of re-querying MySQL and
        # re-tokenising. None in production.
        self._cache_path = cache_path
        self._snapshot_mtime: float = 0.0

        self._loaded_at: float = 0.0
        self._resolver: Optional[FuzzyConceptResolver] = None

        # Shared data updated by postprocess on each refresh; read by all resolvers.
        self._synonym_map: Dict[int, List[str]] = {}
        self._synonym_token_index: Dict[str, List[Any]] = {}
        self._acronym_index: Dict[str, List[str]] = {}
        self._name_token_index: Dict[str, set] = {}
        self._ancestor_map: Dict[int, List[int]] = {}
        self._concepts_by_id: Dict[int, Dict[str, Any]] = {}

    @property
    def synonym_map(self) -> Dict[int, List[str]]:
        return self._synonym_map

    @synonym_map.setter
    def synonym_map(self, value: Dict[int, List[str]]) -> None:
        self._synonym_map = value

    @property
    def synonym_token_index(self) -> Dict[str, List[Any]]:
        return self._synonym_token_index

    @synonym_token_index.setter
    def synonym_token_index(self, value: Dict[str, List[Any]]) -> None:
        self._synonym_token_index = value

    @property
    def acronym_index(self) -> Dict[str, List[str]]:
        return self._acronym_index

    @acronym_index.setter
    def acronym_index(self, value: Dict[str, List[str]]) -> None:
        self._acronym_index = value

    @property
    def name_token_index(self) -> Dict[str, set]:
        return self._name_token_index

    @name_token_index.setter
    def name_token_index(self, value: Dict[str, set]) -> None:
        self._name_token_index = value

    @property
    def ancestor_map(self) -> Dict[int, List[int]]:
        return self._ancestor_map

    @ancestor_map.setter
    def ancestor_map(self, value: Dict[int, List[int]]) -> None:
        self._ancestor_map = value

    @property
    def concepts_by_id(self) -> Dict[int, Dict[str, Any]]:
        return self._concepts_by_id

    @concepts_by_id.setter
    def concepts_by_id(self, value: Dict[int, Dict[str, Any]]) -> None:
        self._concepts_by_id = value

    @property
    def has_loaded_core(self) -> bool:
        return self._core_loaded

    @has_loaded_core.setter
    def has_loaded_core(self, value: bool) -> None:
        self._core_loaded = value

    @property
    def has_loaded_acronyms(self) -> bool:
        return self._acronyms_loaded

    @has_loaded_acronyms.setter
    def has_loaded_acronyms(self, value: bool) -> None:
        self._acronyms_loaded = value

    @property
    def has_loaded_synonyms(self) -> bool:
        return self._synonyms_loaded

    @has_loaded_synonyms.setter
    def has_loaded_synonyms(self, value: bool) -> None:
        self._synonyms_loaded = value

    @property
    def has_loaded_ancestors(self) -> bool:
        return self._ancestors_loaded

    @has_loaded_ancestors.setter
    def has_loaded_ancestors(self, value: bool) -> None:
        self._ancestors_loaded = value

    @property
    def fully_warm(self) -> bool:
        """True once every capability has loaded (core + acronyms + synonyms + ancestors)."""
        return (
            self._core_loaded
            and self._acronyms_loaded
            and self._synonyms_loaded
            and self._ancestors_loaded
        )

    async def get_resolver(self) -> FuzzyConceptResolver:
        now = time.monotonic()
        if self._resolver and (now - self._loaded_at) < self._ttl:
            return self._resolver

        if self._resolver:
            if not self._refresh_task or self._refresh_task.done():
                self._refresh_task = asyncio.create_task(self._refresh())
            return self._resolver

        await self._refresh()
        return self._resolver

    async def run_periodic_refresh(self, skip_initial: bool = False) -> None:
        """Load once, then refresh on the TTL cadence. Runs as a background task.

        Used in development so startup is non-blocking and the refresh is
        observable regardless of the resolver backend. `_refresh` holds the lock
        and swallows/logs its own errors, so a failed refresh keeps the previous
        resolver and the loop continues.
        """
        if not skip_initial:
            log.info("[warmup] background initial load started")
            await self._refresh(label="background-initial")
        while True:
            await asyncio.sleep(self._ttl)
            log.info(
                f"[warmup] periodic background refresh triggered (ttl={self._ttl}s)"
            )
            await self._refresh(label="background-periodic")

    def _read_snapshot(self) -> Optional[Dict[str, Any]]:
        """Load the dev snapshot dict, or None if absent/unreadable. Blocking; run in a thread."""
        try:
            with open(self._cache_path, "rb") as fh:
                return pickle.load(fh)
        except Exception as exc:
            log.info(
                f"[warmup] snapshot read failed ({exc}); falling back to live load"
            )
            return None

    def _write_snapshot(self, data: Dict[str, Any]) -> None:
        """Pickle the fully warmed state to the dev cache path. Blocking; run in a thread."""
        try:
            with open(self._cache_path, "wb") as fh:
                pickle.dump(data, fh, protocol=pickle.HIGHEST_PROTOCOL)
        except Exception as exc:
            log.info(f"[warmup] snapshot write failed: {exc}")

    async def _refresh(self, label: str = "startup") -> None:
        async with self._lock:
            t0 = time.monotonic()

            # Dev-only fast path: restore the whole warmed state from the snapshot cache.
            if self._cache_path and os.path.exists(self._cache_path):
                mtime = os.path.getmtime(self._cache_path)
                if self._resolver and mtime == self._snapshot_mtime:
                    # Unchanged since last load (e.g. periodic refresh) — skip all work.
                    self._loaded_at = time.monotonic()
                    return
                log.info("[warmup] snapshot: reading dev cache")
                snapshot = await asyncio.to_thread(self._read_snapshot)
                if snapshot is not None:
                    try:
                        log.info(
                            "[warmup] snapshot: rebuilding resolver (pretokenised)"
                        )
                        resolver = await asyncio.to_thread(
                            FuzzyConceptResolver,
                            snapshot["concepts"],
                            pretokenised=True,
                        )
                        resolver._store = self
                        self._synonym_map = snapshot["synonym_map"]
                        self._synonym_token_index = snapshot["synonym_token_index"]
                        self._acronym_index = snapshot["acronym_index"]
                        # Old snapshots predate name_token_index — rebuild from the stored
                        # concepts so a stale cache self-heals rather than silently disabling
                        # the fast concept_id IN path.
                        self._name_token_index = snapshot.get(
                            "name_token_index"
                        ) or build_name_token_index(snapshot["concepts"])
                        # ancestor_map is DB-derived and can't be rebuilt from concepts
                        # alone — an old snapshot yields empty children until the next
                        # live refresh. concepts_by_id self-heals from stored concepts.
                        self._ancestor_map = snapshot.get("ancestor_map") or {}
                        self._concepts_by_id = snapshot.get(
                            "concepts_by_id"
                        ) or build_concepts_by_id(snapshot["concepts"])
                        self._resolver = resolver
                        # A snapshot restores every map at once, so all capabilities are ready.
                        self._core_loaded = True
                        self._acronyms_loaded = True
                        self._synonyms_loaded = True
                        self._ancestors_loaded = True
                        self._snapshot_mtime = mtime
                        self._loaded_at = time.monotonic()
                        log.info(
                            f"[warmup] READY — full mode (restored from dev snapshot in "
                            f"{self._loaded_at - t0:.2f}s, {len(resolver.concepts)} concepts; "
                            f"delete {self._cache_path} to rebuild from DB)"
                        )
                        return
                    except Exception as exc:
                        log.error(
                            f"[warmup] snapshot restore failed ({exc}); falling back to live load",
                            exc_info=True,
                        )

            # Skip the reload when the probe matches the last load. Bypassed on first load
            # (no resolver) and on probe error (reload rather than skip blindly).
            fp: Any = None
            if self._fingerprint and self._resolver:
                try:
                    fp = await asyncio.to_thread(self._fingerprint)
                except Exception as exc:
                    log.info(
                        f"[warmup] fingerprint probe failed ({exc}); doing full reload"
                    )
                    fp = None
                if fp is not None and fp == self._last_fingerprint:
                    log.info(
                        "[warmup] data unchanged (fingerprint match) — skipping reload"
                    )
                    self._loaded_at = time.monotonic()
                    return

            # Phase 1 — core: load + tokenise + fast matching indexes. On success we flip the
            # readiness signal immediately and run the slow enrichment loads afterwards.
            try:
                log.info(f"[warmup] loader: querying concepts ({label})")
                concepts = await asyncio.to_thread(self._loader)
                t1 = time.monotonic()
                log.info(
                    f"[warmup] loader done: {t1 - t0:.2f}s ({len(concepts)} concepts)"
                )

                log.info("[warmup] tokenise: building FuzzyConceptResolver")
                resolver = await asyncio.to_thread(FuzzyConceptResolver, concepts)
                resolver._store = self
                t2 = time.monotonic()
                log.info(f"[warmup] tokenise done: {t2 - t1:.2f}s")

                if self._build_core:
                    log.info("[warmup] core: building name/acronym/concepts indexes")
                    await asyncio.to_thread(self._build_core, self, concepts)
            except Exception as exc:
                log.error(f"[warmup] core warm-up failed: {exc}", exc_info=True)
                return

            self._resolver = resolver
            self._loaded_at = time.monotonic()
            log.info(
                f"[warmup] CORE READY — fast matching live ({self._loaded_at - t0:.2f}s);"
                " enrichment (synonyms, ancestors) loading in background"
            )

            # Phase 2 — enrichment: slow serialised DB loads. A failure here leaves core serving;
            # the unloaded flag stays False so it's retried next cycle.
            if self._load_enrichment:
                try:
                    log.info("[warmup] enrichment: loading synonym + ancestor maps")
                    await asyncio.to_thread(self._load_enrichment, self, concepts)
                except Exception as exc:
                    log.error(f"[warmup] enrichment load failed: {exc}", exc_info=True)

            syn_state = "OK" if self._synonym_map else "DISABLED"
            anc_state = "OK" if self._ancestor_map else "DISABLED"
            log.info(
                f"[warmup] READY — full mode ({label} load complete: "
                f"{time.monotonic() - t0:.2f}s; synonyms={syn_state} ancestors={anc_state})"
            )

            # Only record fingerprint + snapshot once fully warm — otherwise a matching probe
            # next cycle would skip the reload and leave enrichment permanently empty.
            if not self.fully_warm:
                return

            # Record this load's fingerprint for the next cycle (on first load fp is
            # still None here, so compute it now).
            if self._fingerprint:
                if fp is None:
                    try:
                        fp = await asyncio.to_thread(self._fingerprint)
                    except Exception as exc:
                        log.info(
                            f"[warmup] fingerprint capture failed ({exc}); next cycle will reload"
                        )
                        fp = None
                self._last_fingerprint = fp

            # Dev-only: persist the fully warmed state so the next reload restores instantly.
            if self._cache_path:
                await asyncio.to_thread(
                    self._write_snapshot,
                    {
                        "concepts": resolver.concepts,
                        "synonym_map": self._synonym_map,
                        "synonym_token_index": self._synonym_token_index,
                        "acronym_index": self._acronym_index,
                        "name_token_index": self._name_token_index,
                        "ancestor_map": self._ancestor_map,
                        "concepts_by_id": self._concepts_by_id,
                    },
                )
                self._snapshot_mtime = (
                    os.path.getmtime(self._cache_path)
                    if os.path.exists(self._cache_path)
                    else 0.0
                )
                log.info(f"[warmup] dev snapshot saved to {self._cache_path}")

    @property
    def resolver(self) -> Optional[FuzzyConceptResolver]:
        return self._resolver
