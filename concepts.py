import json
import math
import os
import re
import time
from typing import Any, Dict, List, Optional

import httpx
import mysql.connector
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

router = APIRouter()

CONCEPT_MATCH_SCORE_EXACT = int(os.getenv("CONCEPT_MATCH_SCORE_EXACT", 1000))
CONCEPT_MATCH_SCORE_CONTAINS = int(os.getenv("CONCEPT_MATCH_SCORE_CONTAINS", 500))
CONCEPT_MATCH_SCORE_PREFIX = int(os.getenv("CONCEPT_MATCH_SCORE_PREFIX", 100))
CONCEPT_MATCH_SCORE_SYNONYM = int(os.getenv("CONCEPT_MATCH_SCORE_SYNONYM", 1000))


class ConceptSearchRequest(BaseModel):
    concept_id: Optional[List[int]] = None
    concept_name: Optional[List[str]] = None
    domain: Optional[str] = None
    collection_ids: Optional[List[int]] = None
    use_collection_filter: bool = False
    use_stats_ordering: bool = False
    page: int = 1
    per_page: int = 25
    include_ancestors: bool = True


class ChildConcept(BaseModel):
    concept_id: int
    name: str
    category: str


class ConceptSearchResult(BaseModel):
    concept_id: int
    name: str
    category: str
    match_score: int
    ncollections: int
    count: Optional[int]
    children: List[ChildConcept] = []


class ConceptSearchResponse(BaseModel):
    total: int
    per_page: int
    current_page: int
    last_page: int
    data: List[ConceptSearchResult]


def _get_db_config(request: Request) -> Dict[str, Any]:
    return request.app.state.db_config


def _get_medcat_names(terms: List[str]) -> List[str]:
    medcat_url = os.getenv("MEDCAT_URL", "").rstrip("/")
    if not medcat_url:
        print("[MedCAT] MEDCAT_URL is not set — skipping expansion")
        return []
    min_acc = float(os.getenv("MEDCAT_MIN_ACC", "0.5"))
    term_set = {t.lower() for t in terms}
    pretty_names = []
    for term in terms:
        try:
            resp = httpx.post(
                f"{medcat_url}/api/process",
                json={"content": {"text": term}},
                timeout=5.0,
            )
            if resp.status_code != 200:
                print(f"[MedCAT] term={term!r} — non-200 response: {resp.status_code}")
                continue
            annotations = resp.json().get("result", {}).get("annotations", [])
            if not annotations:
                print(f"[MedCAT] term={term!r} — no annotations returned")
                continue
            for ann_group in annotations:
                for ann in ann_group.values():
                    acc = ann.get("acc", 0)
                    status = ann.get("meta_anns", {}).get("Status", {}).get("value")
                    pretty_name = ann.get("pretty_name", "")
                    accepted = (
                        acc >= min_acc
                        and status == "Affirmed"
                        and pretty_name.lower() not in term_set
                    )
                    print(
                        f"[MedCAT] term={term!r} pretty_name={pretty_name!r} acc={acc:.3f} status={status} accepted={accepted}"
                    )
                    if accepted:
                        pretty_names.append(pretty_name)
        except Exception as e:
            print(f"[MedCAT] term={term!r} — error: {e}")
    return pretty_names


def find_synonym_concept_ids(
    synonym_map: Dict[int, List[str]], terms: List[str]
) -> List[int]:
    """Return concept_ids whose synonyms contain all tokens from any of the given terms (word-boundary match)."""
    token_sets = [set(term.lower().split()) for term in terms if term.strip()]
    return [
        cid
        for cid, synonyms in synonym_map.items()
        if any(tokens <= set(syn.split()) for tokens in token_sets for syn in synonyms)
    ]


def _normalise_for_like(term: str, strip_s: bool = False) -> str:
    normalised = re.sub(r"[^a-zA-Z0-9]+", "%", term)
    if strip_s and normalised.endswith("s"):
        normalised = normalised[:-1]
    return normalised


def build_where_conditions(
    concept_ids: list,
    concept_names: list,
    medcat_names: list,
    synonym_concept_ids: Optional[List[int]] = None,
) -> tuple:
    conditions: List[str] = []
    bindings: List[Any] = []

    for cid in concept_ids:
        conditions.append("d.concept_id = %s")
        bindings.append(cid)

    for term in concept_names:
        term = term.strip()
        if not term:
            continue
        normalised = _normalise_for_like(term)
        conditions.append("d.description LIKE %s")
        bindings.append(f"%{normalised}%")

    for term in medcat_names:
        term = term.strip()
        if not term:
            continue
        normalised = _normalise_for_like(term, strip_s=True)
        conditions.append("d.description LIKE %s")
        bindings.append(f"%{normalised}%")

    if synonym_concept_ids:
        placeholders = ", ".join(["%s"] * len(synonym_concept_ids))
        conditions.append(f"d.concept_id IN ({placeholders})")
        bindings.extend(synonym_concept_ids)

    return conditions, bindings


def build_score_sql(
    concept_names: list,
    medcat_names: list,
    concept_ids: list,
    synonym_concept_ids: Optional[List[int]] = None,
) -> tuple:
    clauses: List[str] = []
    bindings: List[Any] = []

    for term in concept_names:
        term = term.strip()
        if not term:
            continue
        clauses.append(
            f"""
            CASE
                WHEN LOWER(d.description) = LOWER(%s) THEN {CONCEPT_MATCH_SCORE_EXACT}
                WHEN LOWER(d.description) LIKE LOWER(%s) THEN {CONCEPT_MATCH_SCORE_CONTAINS}
                WHEN LOWER(d.description) LIKE LOWER(%s) THEN {CONCEPT_MATCH_SCORE_PREFIX}
                ELSE 0
            END
            """
        )
        bindings.append(term)
        bindings.append(f"%{term}%")
        bindings.append(f"{term}%")

    for term in medcat_names:
        term = term.strip()
        if not term:
            continue
        normalised = _normalise_for_like(term, strip_s=True)
        clauses.append(
            f"""
            CASE
                WHEN LOWER(d.description) LIKE LOWER(%s) THEN {CONCEPT_MATCH_SCORE_CONTAINS}
                WHEN LOWER(d.description) LIKE LOWER(%s) THEN {CONCEPT_MATCH_SCORE_PREFIX}
                ELSE 0
            END
            """
        )
        bindings.append(f"%{normalised}%")
        bindings.append(f"{normalised}%")

    for cid in concept_ids:
        clauses.append(
            f"""
            CASE
                WHEN d.concept_id = %s THEN {CONCEPT_MATCH_SCORE_EXACT}
                ELSE 0
            END
            """
        )
        bindings.append(cid)

    if synonym_concept_ids:
        placeholders = ", ".join(["%s"] * len(synonym_concept_ids))
        clauses.append(
            f"CASE WHEN d.concept_id IN ({placeholders}) THEN {CONCEPT_MATCH_SCORE_SYNONYM} ELSE 0 END"
        )
        bindings.extend(synonym_concept_ids)

    score_sql = "(" + " + ".join(clauses) + ")" if clauses else "0"
    return score_sql, bindings


def _parse_row(row: dict, include_ancestors: bool) -> "ConceptSearchResult":
    del row["cnt"]

    children: List[ChildConcept] = []
    if include_ancestors:
        raw = row.pop("children", None)
        if raw is not None:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            parsed = json.loads(raw) or []
            children = [ChildConcept(**c) for c in parsed if c is not None]

    return ConceptSearchResult(
        concept_id=int(row["concept_id"]),
        name=row["name"],
        category=row["category"],
        match_score=int(row["match_score"] or 0),
        ncollections=int(row["ncollections"] or 0),
        count=int(row["count"]) if row.get("count") is not None else None,
        children=children,
    )


@router.post("/concepts/search", response_model=ConceptSearchResponse)
def search_concepts(
    payload: ConceptSearchRequest,
    request: Request,
    db_config: Dict[str, Any] = Depends(_get_db_config),
) -> ConceptSearchResponse:
    per_page = min(max(1, payload.per_page), 100)
    page = max(1, payload.page)
    offset = (page - 1) * per_page

    # --- WHERE clause ---
    where = ["d.concept_id IS NOT NULL", "d.concept_id > 0"]
    where_bindings: List[Any] = []

    if payload.use_collection_filter and payload.collection_ids:
        placeholders = ", ".join(["%s"] * len(payload.collection_ids))
        where.append(f"d.collection_id IN ({placeholders})")
        where_bindings.extend(payload.collection_ids)

    if payload.domain:
        where.append("d.category = %s")
        where_bindings.append(payload.domain.lower())

    medcat_names = _get_medcat_names(payload.concept_name or [])

    syn_t0 = time.time()
    syn_concept_ids = find_synonym_concept_ids(
        request.app.state.resolver_store.synonym_map,
        (payload.concept_name or []) + medcat_names,
    )
    print(f"[Concepts] synonym lookup: {(time.time() - syn_t0) * 1000:.1f}ms concept_ids={syn_concept_ids}")

    search_conditions, search_bindings = build_where_conditions(
        payload.concept_id or [],
        payload.concept_name or [],
        medcat_names,
        syn_concept_ids,
    )
    score_sql, score_bindings = build_score_sql(
        payload.concept_name or [],
        medcat_names,
        payload.concept_id or [],
        syn_concept_ids,
    )

    if search_conditions:
        where.append("(" + " OR ".join(search_conditions) + ")")
        where_bindings.extend(search_bindings)

    where_clause = " AND ".join(where)

    # --- Children ---
    if payload.include_ancestors:
        children_join = """
            LEFT JOIN concept_ancestors ca ON ca.parent_concept_id = base.concept_id
            LEFT JOIN distributions dc ON dc.concept_id = ca.child_concept_id
        """
        children_select = """,
            JSON_ARRAYAGG(
                CASE WHEN dc.concept_id IS NOT NULL THEN
                    JSON_OBJECT(
                        'concept_id', dc.concept_id,
                        'name', dc.description,
                        'category', dc.category
                    )
                END
            ) AS children
        """
    else:
        children_join = ""
        children_select = ""

    # --- ORDER BY ---
    if payload.use_stats_ordering:
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

    sql = f"""
        WITH base AS (
            SELECT
                d.concept_id,
                d.description AS name,
                d.category,
                {score_sql} AS match_score,
                COUNT(DISTINCT d.collection_id) AS ncollections,
                SUM(d.count) AS count
            FROM distributions d
            WHERE {where_clause}
            GROUP BY d.concept_id, d.description, d.category
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

    conn = mysql.connector.connect(**db_config)
    try:
        cursor = conn.cursor(dictionary=True)
        main_t0 = time.time()
        cursor.execute(sql, final_bindings)
        rows = cursor.fetchall()
        print(
            f"[Concepts] main query: {(time.time() - main_t0) * 1000:.1f}ms synonym_ids={syn_concept_ids} results={len(rows)}"
        )
    finally:
        conn.close()

    total = int(rows[0]["cnt"]) if rows else 0

    results = [_parse_row(row, payload.include_ancestors) for row in rows]

    last_page = max(1, math.ceil(total / per_page)) if per_page > 0 else 1

    return ConceptSearchResponse(
        total=total,
        per_page=per_page,
        current_page=page,
        last_page=last_page,
        data=results,
    )
