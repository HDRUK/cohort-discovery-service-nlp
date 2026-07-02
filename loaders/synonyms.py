import time
from typing import Any, Dict, List

import mysql.connector


def load_synonym_map(db_config: Dict[str, Any], concept_ids: List[int]) -> Dict[int, List[str]]:
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
            print("[Start-up] concept_synonym table not found — synonym search disabled")
            return {}
        placeholders = ",".join(["%s"] * len(concept_ids))
        t1 = time.monotonic()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            f"SELECT concept_id, concept_synonym_name FROM concept_synonym WHERE concept_id IN ({placeholders})",
            concept_ids,
        )
        rows = cursor.fetchall()
        t2 = time.monotonic()
        result: Dict[int, List[str]] = {}
        for row in rows:
            if row.get("concept_synonym_name"):
                result.setdefault(int(row["concept_id"]), []).append(
                    row["concept_synonym_name"].lower()
                )
        total_syns = sum(len(v) for v in result.values())
        t3 = time.monotonic()
        print(
            f"[Store] Loaded {total_syns} synonyms for {len(result)} concepts"
            f" (query: {t2 - t1:.2f}s, build: {t3 - t2:.2f}s, total: {t3 - t0:.2f}s)"
        )
        return result
    except Exception as e:
        print(f"[Start-up] Failed to load synonym map: {e}")
        return {}
    finally:
        conn.close()
