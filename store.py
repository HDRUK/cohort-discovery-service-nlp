import asyncio
import time
from typing import Any, Dict, List, Optional, Callable
from fuzzy_concept_resolver import FuzzyConceptResolver


class ResolverStore:
    def __init__(self, loader: Callable[[], List[Dict[str, Any]]], ttl_seconds: int):
        self._loader = loader
        self._ttl = ttl_seconds
        self._lock = asyncio.Lock()

        self._loaded_at: float = 0.0
        self._concepts: List[Dict[str, Any]] = []
        self._resolver: Optional[FuzzyConceptResolver] = None

    def get_concepts(self):
        return self._concepts

    async def get_resolver(self) -> FuzzyConceptResolver:
        now = time.monotonic()
        if self._resolver and (now - self._loaded_at) < self._ttl:
            return self._resolver

        async with self._lock:
            now = time.monotonic()
            if self._resolver and (now - self._loaded_at) < self._ttl:
                return self._resolver

            concepts = await asyncio.to_thread(self._loader)

            resolver = await asyncio.to_thread(FuzzyConceptResolver, concepts)

            self._concepts = concepts
            self._resolver = resolver
            self._loaded_at = time.monotonic()
            return resolver
