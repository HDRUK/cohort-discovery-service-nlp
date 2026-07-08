from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class BaseResolver(ABC):
    def __init__(self, store=None):
        self._store = store

    @property
    def acronym_index(self) -> Dict[str, List[str]]:
        return self._store.acronym_index if self._store else {}

    @property
    def synonym_map(self) -> Dict[int, List[str]]:
        return self._store.synonym_map if self._store else {}

    @property
    def synonym_token_index(self) -> Dict[str, List[Any]]:
        return getattr(self._store, "synonym_token_index", {}) if self._store else {}

    @property
    def ancestor_map(self) -> Dict[int, List[int]]:
        return getattr(self._store, "ancestor_map", {}) if self._store else {}

    @property
    def concepts_by_id(self) -> Dict[int, Dict[str, Any]]:
        return getattr(self._store, "concepts_by_id", {}) if self._store else {}

    @abstractmethod
    def search(
        self,
        *,
        concept_names: Optional[List[str]] = None,
        threshold: Optional[float] = None,
        phrase_first: bool = True,
        per_page: int = 25,
        **kwargs,
    ) -> Dict[str, Any]:
        """Run a concept search. Returns {"total": int, "data": [rows]}."""
        ...
