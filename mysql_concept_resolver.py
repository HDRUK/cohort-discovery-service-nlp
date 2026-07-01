import time
from typing import Any, Dict, List, Optional

import mysql.connector

from concepts import (
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

    def search(
        self,
        concept_ids: Optional[List[int]] = None,
        concept_names: Optional[List[str]] = None,
        domain: Optional[str] = None,
        collection_ids: Optional[List[int]] = None,
        use_collection_filter: bool = False,
        use_stats_ordering: bool = False,
        include_ancestors: bool = True,
        page: int = 1,
        per_page: int = 25,
    ) -> Dict[str, Any]:
        """Run a scored concept search. Returns {"total": int, "data": [raw rows]}."""
        concept_ids = concept_ids or []
        concept_names = concept_names or []

        medcat_names = _get_medcat_names(concept_names)

        syn_t0 = time.time()
        syn_concept_ids = find_synonym_concept_ids(
            self._synonym_map, concept_names + medcat_names
        )
        print(
            f"[Resolver] synonym lookup: {(time.time() - syn_t0) * 1000:.1f}ms concept_ids={syn_concept_ids}"
        )

        where = ["d.concept_id IS NOT NULL", "d.concept_id > 0"]
        where_bindings: List[Any] = []

        if use_collection_filter and collection_ids:
            placeholders = ", ".join(["%s"] * len(collection_ids))
            where.append(f"d.collection_id IN ({placeholders})")
            where_bindings.extend(collection_ids)

        if domain:
            where.append("d.domain_id = %s")
            where_bindings.append(domain.lower())

        search_conds, search_bindings = build_where_conditions(
            concept_ids, concept_names, medcat_names, syn_concept_ids
        )
        score_sql, score_bindings = build_score_sql(
            concept_names, medcat_names, concept_ids, syn_concept_ids
        )

        if search_conds:
            where.append("(" + " OR ".join(search_conds) + ")")
            where_bindings.extend(search_bindings)

        where_clause = " AND ".join(where)

        if include_ancestors:
            children_join = """
                LEFT JOIN concept_ancestors ca ON ca.parent_concept_id = base.concept_id
                LEFT JOIN latest_distributions dc ON dc.concept_id = ca.child_concept_id
            """
            children_select = """,
                JSON_ARRAYAGG(
                    CASE WHEN dc.concept_id IS NOT NULL THEN
                        JSON_OBJECT(
                            'concept_id', dc.concept_id,
                            'name', dc.concept_name,
                            'category', dc.domain_id
                        )
                    END
                ) AS children
            """
        else:
            children_join = ""
            children_select = ""

        if use_stats_ordering:
            order_by = """
                ORDER BY
                    base.match_score DESC,
                    base.ncollections DESC,
                    base.count DESC,
                    CHAR_LENGTH(base.name) ASC,
                    base.concept_id
            """
        else:
            order_by = """
                ORDER BY
                    base.match_score DESC,
                    CHAR_LENGTH(base.name) ASC,
                    base.concept_id
            """

        offset = (page - 1) * per_page

        sql = f"""
            WITH base AS (
                SELECT
                    d.concept_id,
                    d.concept_name AS name,
                    d.domain_id AS category,
                    {score_sql} AS match_score,
                    COUNT(DISTINCT d.collection_id) AS ncollections,
                    SUM(d.count) AS count
                FROM latest_distributions d
                WHERE {where_clause}
                GROUP BY d.concept_id, d.concept_name, d.domain_id
            ),
            total AS (
                SELECT COUNT(*) AS cnt FROM base
            )
            SELECT
                base.*,
                total.cnt
                {children_select}
            FROM base
            CROSS JOIN total
            {children_join}
            GROUP BY
                base.concept_id,
                base.name,
                base.category,
                base.match_score,
                base.ncollections,
                base.count,
                total.cnt
            {order_by}
            LIMIT %s OFFSET %s
        """

        final_bindings = score_bindings + where_bindings + [per_page, offset]

        conn = mysql.connector.connect(**self._db_config)
        try:
            cursor = conn.cursor(dictionary=True)
            main_t0 = time.time()
            cursor.execute(sql, final_bindings)
            rows = cursor.fetchall()
            collection_info = f"collection_ids={collection_ids}" if use_collection_filter and collection_ids else "collection_filter=off"
            print(
                f"[Resolver] main query: {(time.time() - main_t0) * 1000:.1f}ms synonym_ids={syn_concept_ids} {collection_info} results={len(rows)}"
            )
        finally:
            conn.close()

        total = int(rows[0]["cnt"]) if rows else 0
        return {"total": total, "data": rows}

    def resolve(
        self,
        text: str,
        threshold: float,
        *,
        phrase_first: bool = True,
        max_matches: Optional[int] = 5,
        use_stats_ordering: bool = False,
        use_collection_filter: bool = False,
        collection_ids: Optional[List[int]] = None,
    ) -> List[Dict[str, Any]]:
        text = text.strip()
        if not text:
            return []

        result = self.search(
            concept_names=[text],
            collection_ids=collection_ids or [],
            use_collection_filter=use_collection_filter,
            use_stats_ordering=use_stats_ordering,
            include_ancestors=False,
            page=1,
            per_page=max_matches if max_matches is not None else 5,
        )

        return [
            {
                "concept_id": int(row["concept_id"]),
                "concept_name": row["name"],
                "domain_id": row["category"],
                "match_score": int(row["match_score"] or 0),
                "ncollections": int(row["ncollections"] or 0),
                "count": int(row["count"]) if row.get("count") is not None else None,
            }
            for row in result["data"]
        ]
