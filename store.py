import asyncio
import time
from typing import Any, Dict, List, Optional, Callable
from resolvers.fuzzy_concept_resolver import FuzzyConceptResolver


class ResolverStore:
    def __init__(
        self,
        loader: Callable[[], List[Dict[str, Any]]],
        ttl_seconds: int,
        postprocess: Optional[Callable[["ResolverStore", List[Dict[str, Any]]], None]] = None,
    ):
        self._loader = loader
        self._ttl = ttl_seconds
        self._postprocess = postprocess
        self._lock = asyncio.Lock()
        self._refresh_task: Optional[asyncio.Task] = None

        self._loaded_at: float = 0.0
        self._resolver: Optional[FuzzyConceptResolver] = None

        # Shared data updated by postprocess on each refresh; read by all resolvers.
        self._synonym_map: Dict[int, List[str]] = {}
        self._acronym_index: Dict[str, List[str]] = {}

    @property
    def synonym_map(self) -> Dict[int, List[str]]:
        return self._synonym_map

    @synonym_map.setter
    def synonym_map(self, value: Dict[int, List[str]]) -> None:
        self._synonym_map = value

    @property
    def acronym_index(self) -> Dict[str, List[str]]:
        return self._acronym_index

    @acronym_index.setter
    def acronym_index(self, value: Dict[str, List[str]]) -> None:
        self._acronym_index = value

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

    async def _refresh(self) -> None:
        async with self._lock:
            try:
                concepts = await asyncio.to_thread(self._loader)
                resolver = await asyncio.to_thread(FuzzyConceptResolver, concepts)
            except Exception as exc:
                print(f"[ResolverStore] Refresh failed: {exc}")
                return

            resolver._store = self

            if self._postprocess:
                try:
                    await asyncio.to_thread(self._postprocess, self, concepts)
                except Exception as exc:
                    print(f"[ResolverStore] Postprocess failed: {exc}")

            self._resolver = resolver
            self._loaded_at = time.monotonic()

    @property
    def resolver(self) -> Optional[FuzzyConceptResolver]:
        return self._resolver
