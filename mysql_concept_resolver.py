from typing import Any, Dict, List, Optional

import mysql.connector

from concepts import build_score_sql, build_where_conditions
from medcat import get_medcat_names


class MySQLConceptResolver:
    def __init__(self, db_config: Dict[str, Any]) -> None:
        self._db_config = db_config
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

        medcat_expansions = get_medcat_names([text])

        search_conds, search_bindings = build_where_conditions([], [text], medcat_expansions)
        if not search_conds:
            return []

        score_sql, score_bindings = build_score_sql([text], medcat_expansions, [])

        sql = f"""
            SELECT
                d.concept_id,
                d.description,
                d.description AS concept_name,
                d.category AS domain_id,
                ({score_sql}) AS match_score,
                COUNT(DISTINCT d.collection_id) AS ncollections,
                SUM(d.count) AS count
            FROM distributions d
            WHERE d.concept_id IS NOT NULL
              AND d.concept_id > 0
              AND ({" OR ".join(search_conds)})
            GROUP BY d.concept_id, d.description, d.category
            HAVING match_score > 0
            ORDER BY match_score DESC, CHAR_LENGTH(d.description) ASC, d.concept_id
            LIMIT %s
        """

        limit = max_matches if max_matches is not None else 5
        bindings = score_bindings + search_bindings + [limit]

        conn = mysql.connector.connect(**self._db_config)
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(sql, bindings)
            rows = cursor.fetchall()
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
