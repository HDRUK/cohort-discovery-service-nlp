import json
import math
from typing import Any, List, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from logging_config import get_logger

log = get_logger()

router = APIRouter()


class ConceptSearchRequest(BaseModel):
    concept_id: Optional[List[int]] = None
    concept_name: Optional[List[str]] = None
    domain: Optional[str] = None
    collection_ids: Optional[List[int]] = None
    use_collection_filter: bool = False
    use_collection_score: bool = True
    use_synonym_lookup: bool = True
    use_medcat: bool = True
    page: int = 1
    per_page: int = 25
    include_ancestors: bool = True


class ChildConcept(BaseModel):
    concept_id: int
    name: str
    category: str


class ConceptSearchResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    concept_id: int
    name: str
    category: str
    match_score: int
    collection_score: int
    ncollections: int
    ncollections_all: int = 0
    count: Optional[int]
    count_all: Optional[int] = None
    children: List[ChildConcept] = []

    @model_validator(mode="before")
    @classmethod
    def _parse_children(cls, data: dict) -> dict:
        raw = data.get("children")
        if raw is None:
            data["children"] = []
        elif isinstance(raw, (bytes, str)):
            decoded = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            data["children"] = [c for c in json.loads(decoded) or [] if c is not None]
        return data

    @field_validator("match_score", "collection_score", "ncollections", "ncollections_all", mode="before")
    @classmethod
    def _coerce_nullable_int(cls, v: Any) -> int:
        return int(v or 0)

    @field_validator("count", "count_all", mode="before")
    @classmethod
    def _coerce_count(cls, v: Any) -> Optional[int]:
        return int(v) if v is not None else None


class ConceptSearchResponse(BaseModel):
    total: int
    per_page: int
    current_page: int
    last_page: int
    data: List[ConceptSearchResult]


def _parse_row(row: dict) -> "ConceptSearchResult":
    return ConceptSearchResult.model_validate(row)


@router.post("/concepts/search", response_model=ConceptSearchResponse)
def search_concepts(
    payload: ConceptSearchRequest,
    request: Request,
) -> ConceptSearchResponse:
    per_page = min(max(1, payload.per_page), 100)
    page = max(1, payload.page)

    store = request.app.state.resolver_store
    backend = getattr(request.app.state, "backend", "sql")
    if backend == "fuzzy":
        resolver = store.resolver or request.app.state.sql_resolver
    else:
        resolver = request.app.state.sql_resolver

    result = resolver.search(
        concept_ids=payload.concept_id,
        concept_names=payload.concept_name,
        domain=payload.domain,
        collection_ids=payload.collection_ids,
        use_collection_filter=payload.use_collection_filter,
        use_collection_score=payload.use_collection_score,
        use_synonym_lookup=payload.use_synonym_lookup,
        use_medcat=payload.use_medcat,
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
        data=[_parse_row(row) for row in result["data"]],
    )
