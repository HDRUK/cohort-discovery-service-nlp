from typing import List, Optional


class FallbackResolver:
    """Wraps a primary resolver and falls back to the store's cached fuzzy resolver when primary returns no matches."""

    def __init__(self, primary, store):
        self._primary = primary
        self._store = store

    @property
    def acronym_index(self):
        return getattr(self._primary, "acronym_index", {})

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
            fallback = self._store.resolver
            if fallback:
                results = fallback.resolve(text, threshold, phrase_first=phrase_first, max_matches=max_matches)
        return results
