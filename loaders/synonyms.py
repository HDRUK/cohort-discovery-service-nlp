import time
from typing import Any, Dict, FrozenSet, List, Tuple

import mysql.connector

from helpers import chunked
from logging_config import get_logger

log = get_logger()


def build_synonym_token_index(
    synonym_map: Dict[int, List[str]],
) -> Dict[str, List[Tuple[int, FrozenSet[str]]]]:
    """Invert synonym_map into an index keyed by token for fast lookup.

    Maps each token -> list of (concept_id, frozenset(synonym_tokens)). A query term
    matches a concept only if all its tokens appear in one synonym, so any matching
    synonym is present in the candidate list of *every* one of the term's tokens —
    scanning the rarest token's list alone is both sufficient and complete.
    """
    index: Dict[str, List[Tuple[int, FrozenSet[str]]]] = {}
    for concept_id, synonyms in synonym_map.items():
        for syn in synonyms:
            tokens = frozenset(syn.split())
            for token in tokens:
                index.setdefault(token, []).append((concept_id, tokens))
    return index


def load_synonym_map(
    db_config: Dict[str, Any], concept_ids: List[int]
) -> Dict[int, List[str]]:
    """Load synonyms for the given concept_ids from concept_synonym, keyed by concept_id.

    Bounded by the distribution view concept set so we never scan the full table.
    Returns {} if the table doesn't exist or on any error.
    """
    if not concept_ids:
        return {}
    t0 = time.monotonic()
    conn = mysql.connector.connect(**db_config)
    try:
        cursor = conn.cursor()
        cursor.execute("SHOW TABLES LIKE 'concept_synonym'")
        exists = cursor.fetchone()
        cursor.close()
        if not exists:
            log.warning(
                "[Start-up] concept_synonym table not found — synonym search disabled"
            )
            return {}
        t1 = time.monotonic()
        # Query in bounded id-chunks rather than one giant IN (...) clause, which is slow for
        # mysql.connector to bind and MySQL to parse over the full concept set.
        cursor = conn.cursor(dictionary=True)
        rows = []
        for chunk in chunked(concept_ids):
            placeholders = ",".join(["%s"] * len(chunk))
            cursor.execute(
                f"SELECT concept_id, concept_synonym_name FROM concept_synonym WHERE concept_id IN ({placeholders})",
                chunk,
            )
            rows.extend(cursor.fetchall())
        t2 = time.monotonic()
        result: Dict[int, List[str]] = {}
        for row in rows:
            if row.get("concept_synonym_name"):
                result.setdefault(int(row["concept_id"]), []).append(
                    row["concept_synonym_name"].lower()
                )
        total_syns = sum(len(v) for v in result.values())
        t3 = time.monotonic()
        log.info(
            f"[Store] Loaded {total_syns} synonyms for {len(result)} concepts"
            f" (query: {t2 - t1:.2f}s, build: {t3 - t2:.2f}s, total: {t3 - t0:.2f}s)"
        )
        return result
    except Exception as e:
        log.error(f"[Start-up] Failed to load synonym map: {e}", exc_info=True)
        return {}
    finally:
        conn.close()
