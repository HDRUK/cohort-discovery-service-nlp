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

    @abstractmethod
    def resolve(
        self,
        text: str,
        threshold: Any,
        *,
        phrase_first: bool = True,
        max_matches: Optional[int] = None,
        **kwargs,
    ) -> List[Dict[str, Any]]:
        ...
