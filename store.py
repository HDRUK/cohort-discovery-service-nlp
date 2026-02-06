import asyncio
import time
from typing import Any, Dict, List, Optional, Callable
from fuzzy_concept_resolver import FuzzyConceptResolver


class ResolverStore:
    def __init__(
        self,
        loader: Callable[[], List[Dict[str, Any]]],
        ttl_seconds: int,
        postprocess: Optional[Callable[[FuzzyConceptResolver, List[Dict[str, Any]]], None]] = None,
    ):
        self._loader = loader
        self._ttl = ttl_seconds
        self._postprocess = postprocess
        self._lock = asyncio.Lock()
        self._refresh_task: Optional[asyncio.Task] = None

        self._loaded_at: float = 0.0
        self._concepts: List[Dict[str, Any]] = []
        self._resolver: Optional[FuzzyConceptResolver] = None

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

            if self._postprocess:
                try:
                    self._postprocess(resolver, concepts)
                except Exception as exc:
                    print(f"[ResolverStore] Postprocess failed: {exc}")

            self._concepts = concepts
            self._resolver = resolver
            self._loaded_at = time.monotonic()
