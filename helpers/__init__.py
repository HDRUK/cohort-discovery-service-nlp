from typing import List

from config import LOADER_CHUNK_SIZE


def chunked(seq: List, size: int = LOADER_CHUNK_SIZE):
    """Yield successive `size`-length slices of `seq`.

    Used by the concept loaders to split a large concept-id list into bounded
    `IN (...)` queries, avoiding a single huge placeholder list.
    """
    for start in range(0, len(seq), size):
        yield seq[start : start + size]
