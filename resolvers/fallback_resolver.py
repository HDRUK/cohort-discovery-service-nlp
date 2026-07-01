from typing import List, Optional

from resolvers.base_resolver import BaseResolver


class FallbackResolver(BaseResolver):
    """Wraps a primary resolver and falls back to the store's cached fuzzy resolver when primary returns no matches."""

    def __init__(self, primary, store):
        super().__init__(store)
        self._primary = primary

    def resolve(
        self,
        text,
        threshold,
        *,
        phrase_first=True,
        max_matches=None,
        use_stats_ordering: bool = False,
        use_collection_filter: bool = False,
        collection_ids: Optional[List[int]] = None,
        **kwargs,
    ):
        results = self._primary.resolve(
            text,
            threshold,
            phrase_first=phrase_first,
            max_matches=max_matches,
            use_stats_ordering=use_stats_ordering,
            use_collection_filter=use_collection_filter,
            collection_ids=collection_ids,
        )
        if not results:
            fallback = self._store.resolver if self._store else None
            if fallback:
                results = fallback.resolve(text, threshold, phrase_first=phrase_first, max_matches=max_matches)
        return results
