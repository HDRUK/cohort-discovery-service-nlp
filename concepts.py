import json
import math
import os
import re
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter()

CONCEPT_MATCH_SCORE_EXACT = int(os.getenv("CONCEPT_MATCH_SCORE_EXACT", 1000))
CONCEPT_MATCH_SCORE_CONTAINS = int(os.getenv("CONCEPT_MATCH_SCORE_CONTAINS", 500))
CONCEPT_MATCH_SCORE_PREFIX = int(os.getenv("CONCEPT_MATCH_SCORE_PREFIX", 100))
CONCEPT_MATCH_SCORE_SYNONYM = int(os.getenv("CONCEPT_MATCH_SCORE_SYNONYM", 1000))
CONCEPT_MATCH_SCORE_TOKEN = int(os.getenv("CONCEPT_MATCH_SCORE_TOKEN", 50))

_rules_path = os.getenv("RULES_PATH", "rules.json")
with open(_rules_path) as _f:
    _medcat_rules = json.load(_f).get("medcat", {})

_MEDCAT_TOKEN_STOPWORDS = set(_medcat_rules.get("token_stopwords", []))
_MEDCAT_TOKEN_MIN_LEN: int = _medcat_rules.get("token_min_len", 8)


def _extract_medcat_tokens(terms: List[str]) -> List[str]:
    seen: set = set()
    tokens = []
    for term in terms:
        for word in re.sub(r"[^a-zA-Z]", " ", term).lower().split():
            if (
                len(word) >= _MEDCAT_TOKEN_MIN_LEN
                and word not in _MEDCAT_TOKEN_STOPWORDS
                and word not in seen
            ):
                seen.add(word)
                tokens.append(word)
    return tokens


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
        conditions.append("d.concept_name LIKE %s")
        bindings.append(f"%{normalised}%")

    for term in medcat_names:
        term = term.strip()
        if not term:
            continue
        normalised = _normalise_for_like(term, strip_s=True)
        conditions.append("d.concept_name LIKE %s")
        bindings.append(f"%{normalised}%")

    for token in _extract_medcat_tokens(medcat_names):
        conditions.append("d.concept_name LIKE %s")
        bindings.append(f"%{token}%")

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
                WHEN LOWER(d.concept_name) = LOWER(%s) THEN {CONCEPT_MATCH_SCORE_EXACT}
                WHEN LOWER(d.concept_name) LIKE LOWER(%s) THEN {CONCEPT_MATCH_SCORE_CONTAINS}
                WHEN LOWER(d.concept_name) LIKE LOWER(%s) THEN {CONCEPT_MATCH_SCORE_PREFIX}
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
                WHEN LOWER(d.concept_name) LIKE LOWER(%s) THEN {CONCEPT_MATCH_SCORE_CONTAINS}
                WHEN LOWER(d.concept_name) LIKE LOWER(%s) THEN {CONCEPT_MATCH_SCORE_PREFIX}
                ELSE 0
            END
            """
        )
        bindings.append(f"%{normalised}%")
        bindings.append(f"{normalised}%")

    for token in _extract_medcat_tokens(medcat_names):
        clauses.append(
            f"CASE WHEN LOWER(d.concept_name) LIKE LOWER(%s) THEN {CONCEPT_MATCH_SCORE_TOKEN} ELSE 0 END"
        )
        bindings.append(f"%{token}%")

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
) -> ConceptSearchResponse:
    per_page = min(max(1, payload.per_page), 100)
    page = max(1, payload.page)

    resolver = request.app.state.sql_resolver

    result = resolver.search(
        concept_ids=payload.concept_id,
        concept_names=payload.concept_name,
        domain=payload.domain,
        collection_ids=payload.collection_ids,
        use_collection_filter=payload.use_collection_filter,
        use_stats_ordering=payload.use_stats_ordering,
        include_ancestors=payload.include_ancestors,
        page=page,
        per_page=per_page,
    )

    total = result["total"]
    last_page = max(1, math.ceil(total / per_page)) if per_page > 0 else 1

    return ConceptSearchResponse(
        total=total,
        per_page=per_page,
        current_page=page,
        last_page=last_page,
        data=[_parse_row(row, payload.include_ancestors) for row in result["data"]],
    )
