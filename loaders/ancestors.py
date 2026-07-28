import time
from typing import Any, Dict, List

import mysql.connector

from helpers import chunked
from logging_config import get_logger

log = get_logger()


def load_ancestor_map(
    db_config: Dict[str, Any], concept_ids: List[int]
) -> Dict[int, List[int]]:
    """Load parent -> [child_concept_id, ...] edges from concept_ancestor.

    Bounded to concept_ids: the parent side is filtered in SQL; the child side is
    filtered in Python (avoids a costly double-IN cross-filter over the full id list).
    Self-referential rows are excluded. Returns {} if the table is missing or on error.
    """
    if not concept_ids:
        return {}
    concept_id_set = set(concept_ids)
    t0 = time.monotonic()
    conn = None
    try:
        log.debug(
            f"[Start-up] Loading concept_ancestor from db='{db_config.get('database')}'"
            f" on host='{db_config.get('host')}'"
        )
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        cursor.execute("SHOW TABLES LIKE 'concept_ancestor'")
        exists = cursor.fetchone()
        cursor.close()
        if not exists:
            log.warning(
                "[Start-up] concept_ancestor table not found — child concepts disabled"
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
                f"SELECT ancestor_concept_id, descendant_concept_id FROM concept_ancestor"
                f" WHERE ancestor_concept_id IN ({placeholders})"
                f" AND ancestor_concept_id != descendant_concept_id"
                f" AND min_levels_of_separation = 1",
                chunk,
            )
            rows.extend(cursor.fetchall())
        t2 = time.monotonic()
        # Dedup children per parent; skip descendants not in the loaded concept set.
        acc: Dict[int, set] = {}
        for row in rows:
            child = int(row["descendant_concept_id"])
            if child not in concept_id_set:
                continue
            acc.setdefault(int(row["ancestor_concept_id"]), set()).add(child)
        result: Dict[int, List[int]] = {
            pid: sorted(children) for pid, children in acc.items()
        }
        total_edges = sum(len(v) for v in result.values())
        t3 = time.monotonic()
        log.info(
            f"[Store] Loaded {total_edges} ancestor edges for {len(result)} parent concepts"
            f" (query: {t2 - t1:.2f}s, build: {t3 - t2:.2f}s, total: {t3 - t0:.2f}s)"
        )
        return result
    except Exception as e:
        log.error(f"[Start-up] Failed to load ancestor map: {e}", exc_info=True)
        return {}
    finally:
        if conn is not None:
            conn.close()
