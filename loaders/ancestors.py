import time
from typing import Any, Dict, List

import mysql.connector

from logging_config import get_logger

log = get_logger()


def load_ancestor_map(db_config: Dict[str, Any], concept_ids: List[int]) -> Dict[int, List[int]]:
    """Load parent -> [child_concept_id, ...] edges from concept_ancestors.

    Bounded to the given concept_ids on BOTH sides: the child side reproduces the
    old SQL's "child must exist in latest_distributions" filter (concept_ids IS the
    latest_distributions id set), and the parent side is a size optimisation (parents
    outside the result set can never be a base row). Self-referential rows
    (parent == child) are excluded so a concept never lists itself as its own child.
    Returns {} if the table doesn't exist or on any error.
    """
    if not concept_ids:
        return {}
    t0 = time.monotonic()
    conn = mysql.connector.connect(**db_config)
    try:
        cursor = conn.cursor()
        cursor.execute("SHOW TABLES LIKE 'concept_ancestors'")
        exists = cursor.fetchone()
        cursor.close()
        if not exists:
            log.warning("[Start-up] concept_ancestors table not found — child concepts disabled")
            return {}
        placeholders = ",".join(["%s"] * len(concept_ids))
        t1 = time.monotonic()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            f"SELECT parent_concept_id, child_concept_id FROM concept_ancestors"
            f" WHERE parent_concept_id IN ({placeholders})"
            f" AND child_concept_id IN ({placeholders})"
            f" AND parent_concept_id != child_concept_id",
            concept_ids + concept_ids,
        )
        rows = cursor.fetchall()
        t2 = time.monotonic()
        # Dedup children per parent (accumulate into sets, then materialise sorted lists).
        acc: Dict[int, set] = {}
        for row in rows:
            acc.setdefault(int(row["parent_concept_id"]), set()).add(int(row["child_concept_id"]))
        result: Dict[int, List[int]] = {pid: sorted(children) for pid, children in acc.items()}
        total_edges = sum(len(v) for v in result.values())
        t3 = time.monotonic()
        log.info(
            f"[Store] Loaded {total_edges} ancestor edges for {len(result)} parent concepts"
            f" (query: {t2 - t1:.2f}s, build: {t3 - t2:.2f}s, total: {t3 - t0:.2f}s)"
        )
        return result
    except Exception as e:
        log.warning(f"[Start-up] Failed to load ancestor map: {e}")
        return {}
    finally:
        conn.close()
