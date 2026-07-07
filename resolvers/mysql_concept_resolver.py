import time
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.engine import Engine

from logging_config import get_logger
from resolvers.base_resolver import BaseResolver
from resolvers.medcat_client import MedCATClient
from resolvers.sql_helpers import (
    CollectionScore,
    SqlFragment,
    build_collection_score_cte,
    build_match_score_sql,
    build_where_conditions,
    find_name_match_concept_ids,
    find_synonym_concept_ids,
)

log = get_logger()


class MySQLConceptResolver(BaseResolver):
    def __init__(
        self,
        engine: Engine,
        store=None,
        medcat_client: Optional[MedCATClient] = None,
    ) -> None:
        super().__init__(store)
        self._engine = engine
        self._medcat_client = medcat_client

    def search(
        self,
        concept_ids: Optional[List[int]] = None,
        concept_names: Optional[List[str]] = None,
        domain: Optional[str] = None,
        collection_ids: Optional[List[int]] = None,
        use_collection_filter: bool = False,
        use_collection_score: bool = True,
        use_synonym_lookup: bool = True,
        use_medcat: bool = True,
        include_ancestors: bool = False,
        page: int = 1,
        per_page: int = 25,
        threshold: Optional[float] = None,
        phrase_first: bool = True,
        **ignored,
    ) -> Dict[str, Any]:
        """Run a scored concept search. Returns {"total": int, "data": [raw rows]}.

        `threshold`/`phrase_first` are accepted for interface parity with the fuzzy
        resolver and ignored here (scoring is done in SQL).
        """
        concept_ids = concept_ids or []
        concept_names = concept_names or []

        medcat_names = self._expand_medcat(concept_names, use_medcat)
        syn_concept_ids = self._synonym_ids(
            concept_names + medcat_names, use_synonym_lookup
        )

        candidate_ids, search = self._candidate_conditions(
            concept_ids, concept_names, medcat_names, syn_concept_ids
        )
        # Empty set means the in-memory pre-filter ran and matched nothing.
        if candidate_ids is not None and not candidate_ids:
            return {"total": 0, "data": []}

        where = self._where(domain, collection_ids, use_collection_filter, search)
        score = build_match_score_sql(concept_names, medcat_names, concept_ids, syn_concept_ids)
        collection = (
            build_collection_score_cte(collection_ids, candidate_ids)
            if use_collection_score
            else CollectionScore.empty()
        )

        count_sql = f"""
            SELECT COUNT(DISTINCT d.concept_id) AS cnt
            FROM latest_distributions d
            {where.sql}
        """
        data_sql, data_bindings = self._data_query(
            where, score, collection, page, per_page, use_collection_score
        )

        log_context = (
            f"collection_filter={use_collection_filter}"
            f" collection_score={use_collection_score}"
            f" ancestors={include_ancestors}"
            f" collection_ids={collection_ids}"
        )
        result = self._execute(count_sql, where.bindings, data_sql, data_bindings, log_context)
        if include_ancestors:
            self._attach_children(result["data"])
        return result

    # -- steps -----------------------------------------------------------------

    def _expand_medcat(self, concept_names: List[str], use_medcat: bool) -> List[str]:
        if use_medcat and self._medcat_client:
            return self._medcat_client.expand(concept_names)
        return []

    def _synonym_ids(
        self, terms: List[str], enabled: bool
    ) -> Optional[List[int]]:
        if not enabled:
            return None
        syn_t0 = time.monotonic()
        syn_concept_ids = find_synonym_concept_ids(self.synonym_token_index, terms)
        log.info(
            f"[Resolver] synonym lookup: {(time.monotonic() - syn_t0) * 1000:.1f}ms enabled={enabled}"
        )
        return syn_concept_ids

    def _candidate_conditions(
        self,
        concept_ids: List[int],
        concept_names: List[str],
        medcat_names: List[str],
        syn_concept_ids: Optional[List[int]],
    ) -> Tuple[Optional[set], SqlFragment]:
        """Resolve the row-selection conditions.

        Fast path: when the warm name-token index is available, resolve candidate
        concept_ids in memory and select them with a single `concept_id IN (...)`.
        Reduced-mode fallback: LIKE conditions via build_where_conditions().

        Returns (candidate_ids, fragment). candidate_ids is a set on the fast path
        (empty set means "matched nothing", so the caller can short-circuit) or None
        when the fallback was used.
        """
        name_token_index = (
            getattr(self._store, "name_token_index", {}) if self._store else {}
        )
        has_name_search = bool(
            concept_names or medcat_names or syn_concept_ids or concept_ids
        )

        if name_token_index and has_name_search:
            filter_t0 = time.monotonic()
            candidate_ids = find_name_match_concept_ids(
                name_token_index, concept_names, medcat_names
            )
            candidate_ids.update(syn_concept_ids or [])
            candidate_ids.update(concept_ids or [])
            log.debug(
                f"[Resolver] in-memory candidate filter: {(time.monotonic() - filter_t0) * 1000:.1f}ms"
                f" index_tokens={len(name_token_index)} candidates={len(candidate_ids)}"
            )
            if not candidate_ids:
                return candidate_ids, SqlFragment("", [])
            placeholders = ", ".join(["%s"] * len(candidate_ids))
            conds = [f"d.concept_id IN ({placeholders})"]
            bindings: List[Any] = list(candidate_ids)
        else:
            candidate_ids = None
            conds, bindings = build_where_conditions(
                concept_ids, concept_names, medcat_names, syn_concept_ids
            )

        sql = "(" + " OR ".join(conds) + ")" if conds else ""
        return candidate_ids, SqlFragment(sql, bindings)

    def _where(
        self,
        domain: Optional[str],
        collection_ids: Optional[List[int]],
        use_collection_filter: bool,
        search: SqlFragment,
    ) -> SqlFragment:
        """Assemble the WHERE clause: collection filter, domain, then the search
        fragment — in that binding order."""
        conds: List[str] = []
        bindings: List[Any] = []

        if use_collection_filter and collection_ids:
            placeholders = ", ".join(["%s"] * len(collection_ids))
            conds.append(f"d.collection_id IN ({placeholders})")
            bindings.extend(collection_ids)

        if domain:
            conds.append("d.domain_id = %s")
            bindings.append(domain.lower())

        if search.sql:
            conds.append(search.sql)
            bindings.extend(search.bindings)

        clause = " AND ".join(conds)
        return SqlFragment(f"WHERE {clause}" if clause else "", bindings)

    def _attach_children(self, rows: List[Dict[str, Any]]) -> None:
        """Hydrate each row's `children` from the in-memory ancestor map.

        Runs post-LIMIT so children are built only for the returned page. Child ids
        are mapped through concepts_by_id to the {concept_id, name, category} shape
        ChildConcept expects; ids absent from concepts_by_id are dropped defensively."""
        ancestor_map = self.ancestor_map
        concepts_by_id = self.concepts_by_id
        for row in rows:
            child_ids = ancestor_map.get(row["concept_id"], [])
            row["children"] = [
                concepts_by_id[cid] for cid in child_ids if cid in concepts_by_id
            ]

    def _data_query(
        self,
        where: SqlFragment,
        score: SqlFragment,
        collection: CollectionScore,
        page: int,
        per_page: int,
        use_collection_score: bool,
    ) -> Tuple[str, List[Any]]:
        offset = (page - 1) * per_page
        with_clause = f"WITH {collection.cte.sql}" if collection.cte.sql else "WITH"
        order_score = (
            "(base.match_score + base.collection_score)"
            if use_collection_score
            else "base.match_score"
        )

        data_sql = f"""
            {with_clause} base AS (
                SELECT
                    d.concept_id,
                    d.concept_name as name,
                    d.domain_id as category,
                    {score.sql} AS match_score,
                    {collection.score_expr} AS collection_score,
                    COUNT(DISTINCT d.collection_id) AS ncollections,
                    SUM(d.count) AS count
                FROM latest_distributions d
                {collection.join}
                {where.sql}
                GROUP BY d.concept_id, d.concept_name, d.domain_id{collection.group_cols}
            )
            SELECT base.*
            FROM base
            ORDER BY
                {order_score} DESC,
                CHAR_LENGTH(base.name) ASC,
                base.concept_id
            LIMIT %s OFFSET %s
        """

        # Bindings mirror placeholder order in data_sql: CTE, score, WHERE, LIMIT/OFFSET.
        data_bindings = [
            *collection.cte.bindings,
            *score.bindings,
            *where.bindings,
            per_page,
            offset,
        ]
        return data_sql, data_bindings

    def _execute(
        self,
        count_sql: str,
        count_bindings: List[Any],
        data_sql: str,
        data_bindings: List[Any],
        log_context: str,
    ) -> Dict[str, Any]:
        raw_conn = self._engine.raw_connection()
        try:
            cursor = raw_conn.cursor(dictionary=True)

            count_t0 = time.monotonic()
            cursor.execute(count_sql, count_bindings)
            count_row = cursor.fetchone()
            total = int(count_row["cnt"]) if count_row else 0
            log.debug(
                f"[Resolver] count query: {(time.monotonic() - count_t0) * 1000:.1f}ms total={total}"
            )

            if total == 0:
                return {"total": 0, "data": []}

            main_t0 = time.monotonic()
            cursor.execute(data_sql, data_bindings)
            rows = cursor.fetchall()
            log.info(
                f"[Resolver] main query: {(time.monotonic() - main_t0) * 1000:.1f}ms"
                f" {log_context} results={len(rows)}"
            )
        finally:
            raw_conn.close()

        return {"total": total, "data": rows}
