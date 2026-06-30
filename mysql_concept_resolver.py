import time
from typing import Any, Dict, List, Optional

import mysql.connector

from concepts import (
    LOG_MATCHES,
    _get_medcat_names,
    build_score_sql,
    build_where_conditions,
    find_synonym_concept_ids,
)


class MySQLConceptResolver:
    def __init__(
        self,
        db_config: Dict[str, Any],
        synonym_map: Optional[Dict[int, List[str]]] = None,
    ) -> None:
        self._db_config = db_config
        self._synonym_map: Dict[int, List[str]] = synonym_map or {}
        self.acronym_index: Dict[str, List[str]] = {}

    def resolve(
        self,
        text: str,
        threshold: float,
        *,
        phrase_first: bool = True,
        max_matches: Optional[int] = 5,
    ) -> List[Dict[str, Any]]:
        text = text.strip()
        if not text:
            return []

        medcat_expansions = _get_medcat_names([text])

        search_conds, search_bindings = build_where_conditions([], [text], medcat_expansions)
        if not search_conds:
            return []

        score_sql, score_bindings = build_score_sql([text], medcat_expansions, [])

        conn = mysql.connector.connect(**self._db_config)
        try:
            syn_t0 = time.time()
            syn_concept_ids = find_synonym_concept_ids(self._synonym_map, [text] + medcat_expansions)
            print(f"[Resolver] synonym lookup: {(time.time() - syn_t0) * 1000:.1f}ms concept_ids={syn_concept_ids}")

            syn_where_conds: List[str] = []
            syn_where_bindings: List[Any] = []
            syn_score_parts: List[str] = []
            syn_score_bindings: List[Any] = []

            if syn_concept_ids:
                placeholders = ", ".join(["%s"] * len(syn_concept_ids))
                syn_where_conds.append(f"d.concept_id IN ({placeholders})")
                syn_where_bindings.extend(syn_concept_ids)
                syn_score_parts.append(f"CASE WHEN d.concept_id IN ({placeholders}) THEN 500 ELSE 0 END")
                syn_score_bindings.extend(syn_concept_ids)

            all_where_conds = search_conds + syn_where_conds
            if syn_score_parts:
                full_score_sql = f"{score_sql} + {' + '.join(syn_score_parts)}"
            else:
                full_score_sql = score_sql

            sql = f"""
                SELECT
                    d.concept_id,
                    d.description,
                    d.description AS concept_name,
                    d.category AS domain_id,
                    ({full_score_sql}) AS match_score,
                    COUNT(DISTINCT d.collection_id) AS ncollections,
                    SUM(d.count) AS count
                FROM distributions d
                WHERE d.concept_id IS NOT NULL
                  AND d.concept_id > 0
                  AND ({" OR ".join(all_where_conds)})
                GROUP BY d.concept_id, d.description, d.category
                HAVING match_score > 0
                ORDER BY match_score DESC, CHAR_LENGTH(d.description) ASC, d.concept_id
                LIMIT %s
            """

            limit = max_matches if max_matches is not None else 5
            bindings = score_bindings + syn_score_bindings + search_bindings + syn_where_bindings + [limit]

            cursor = conn.cursor(dictionary=True)
            main_t0 = time.time()
            cursor.execute(sql, bindings)
            rows = cursor.fetchall()
            print(f"[Resolver] main query: {(time.time() - main_t0) * 1000:.1f}ms synonym_ids={syn_concept_ids} results={len(rows)}")
            if LOG_MATCHES:
                for row in rows:
                    print(f"[Resolver]   concept_id={row['concept_id']} name={row['description']!r} score={row['match_score']}")
        finally:
            conn.close()

        return [
            {
                "concept_id": int(row["concept_id"]),
                "concept_name": row["concept_name"],
                "description": row["description"],
                "domain_id": row["domain_id"],
                "match_score": int(row["match_score"] or 0),
                "ncollections": int(row["ncollections"] or 0),
                "count": int(row["count"]) if row.get("count") is not None else None,
            }
            for row in rows
        ]
