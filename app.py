import os
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Query, Request
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.engine import URL as EngineURL

from concepts import router as concepts_router
from loaders.concepts import load_concepts_from_mysql
from loaders.synonyms import load_synonym_map
from parsing import QueryParser
from resolvers import FallbackResolver, MySQLConceptResolver
from rules_engine import RuleEngine
from store import ResolverStore


# Load environment variables
load_dotenv()

STORE_REFRESH_TTL = int(os.getenv("STORE_REFRESH_TTL", 60))
RESOLVER_BACKEND = os.getenv("RESOLVER_BACKEND", "sql")

# MySQL config
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASS"),
    "database": os.getenv("DB_NAME"),
    "port": int(os.getenv("DB_PORT", 3306)),
}

# OMOP vocabulary DB — may differ from the app DB (e.g. concept_synonym lives here).
# Falls back to DB_NAME if OMOP_DB_NAME is not set.
OMOP_DB_CONFIG = {
    **DB_CONFIG,
    "database": os.getenv("OMOP_DB_NAME", os.getenv("DB_NAME")),
}

DEFAULT_THRESHOLD = int(os.getenv("DEFAULT_THRESHOLD", 90))


def _build_engine(cfg: dict):
    url = EngineURL.create(
        drivername="mysql+mysqlconnector",
        username=cfg["user"],
        password=cfg["password"],
        host=cfg["host"],
        port=cfg["port"],
        database=cfg["database"],
    )
    return create_engine(url, pool_size=5, max_overflow=10, pool_pre_ping=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db_engine = _build_engine(DB_CONFIG)
    app.state.db_engine = db_engine

    def enrich_resolver(store, concepts):
        store.synonym_map = load_synonym_map(OMOP_DB_CONFIG, [c["concept_id"] for c in concepts])
        store.acronym_index = ENGINE.build_acronym_index(concepts)

    # Concepts are loaded at startup for three reasons:
    #   1. FuzzyConceptResolver — iterates the full list on every resolve (RESOLVER_BACKEND=fuzzy).
    #   2. synonym_map — built from concept IDs; also used by MySQLConceptResolver at request time.
    #   3. acronym_index — built from concept names; used by QueryParser for acronym expansion.
    # If RESOLVER_BACKEND=sql is permanent and the fuzzy fallback in FallbackResolver is removed,
    # this load could be replaced by a lighter synonym-only query. Refactor candidate.
    store = ResolverStore(
        lambda: load_concepts_from_mysql(db_engine),
        ttl_seconds=STORE_REFRESH_TTL,
        postprocess=enrich_resolver,
    )
    resolver = await store.get_resolver()
    app.state.resolver_store = store
    app.state.sql_resolver = MySQLConceptResolver(db_engine, store)
    app.state.fallback_resolver = FallbackResolver(app.state.sql_resolver, store)
    print(
        f"[Start-up] Loaded FuzzyConceptResolver (concepts={len(resolver.concepts)}) from `distribution_concepts`"
    )
    yield


def get_resolver_store(request: Request) -> ResolverStore:
    return request.app.state.resolver_store


# FastAPI app
app = FastAPI(title="Project Daphne NLP Service", version="1.0", lifespan=lifespan)
app.include_router(concepts_router)

# Parsing engine
ENGINE = RuleEngine()
PARSER = QueryParser(ENGINE)


# ------------------------------------------------------------
# Pydantic models
# ------------------------------------------------------------
class QueryRequest(BaseModel):
    query: str
    use_collection_filter: bool = False
    collection_ids: Optional[List[int]] = None


class Entity(BaseModel):
    text: str
    label: Optional[str] = None
    start: int
    end: int
    attributes: Dict[str, Any]
    age_constraints: List[Dict[str, Any]] = []
    time_constraints: List[Dict[str, Any]] = []
    negated: bool = False


class Group(BaseModel):
    text: str
    operator: Optional[str] = None
    entities: List[Entity]
    age_constraints: List[Dict[str, Any]] = []
    time_constraints: List[Dict[str, Any]] = []


class RootGroup(BaseModel):
    entities: List[Entity] = []
    groups: List[Group] = []
    age_constraints: List[Dict[str, Any]] = []
    time_constraints: List[Dict[str, Any]] = []


class QueryResponse(BaseModel):
    entities: List[Entity]
    groups: List[Group] = []
    root_operator: Optional[str] = None
    root_groups: List[RootGroup] = []
    warnings: List[str] = []
    age_constraints: List[Dict[str, Any]] = []
    time_constraints: List[Dict[str, Any]] = []


class AcronymEntry(BaseModel):
    acronym: str
    concepts: List[str]


class AcronymResponse(BaseModel):
    total: int
    items: List[AcronymEntry]


# ------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------
@app.post("/extract", response_model=QueryResponse)
async def extract_entities(
    payload: QueryRequest,
    request: Request,
    threshold: float = Query(
        DEFAULT_THRESHOLD, description="Fuzzy match threshold 0-100"
    ),
    phrase_first: bool = Query(
        True, description="Prefer phrase overlap when token matching is available"
    ),
    max_matches: Optional[int] = Query(
        None,
        ge=1,
        description="Max concept matches per entity (defaults to RESOLVER_MAX_MATCHES)",
    ),
    store: ResolverStore = Depends(get_resolver_store),
):
    """
    Extract clinical concepts from query.
    Set RESOLVER_BACKEND=sql (default) for MySQL+MedCAT lookup,
    or RESOLVER_BACKEND=fuzzy for in-memory fuzzy matching.
    """
    if RESOLVER_BACKEND == "fuzzy":
        resolver = await store.get_resolver()
        ret_value = PARSER.extract(payload.query, threshold, phrase_first, resolver, max_matches=max_matches)
    else:
        ret_value = PARSER.extract(
            payload.query,
            threshold,
            phrase_first,
            request.app.state.fallback_resolver,
            max_matches=max_matches,
            use_collection_filter=payload.use_collection_filter,
            collection_ids=list(payload.collection_ids) if payload.collection_ids else [],
        )

    print(f"[Request] query='{payload.query}' => entities={ret_value}")

    return ret_value


@app.get("/")
def root():
    return {
        "message": "Cohort Discovery NLP Service running. POST to /extract with {query: 'your text'}"
    }


@app.get("/acronyms", response_model=AcronymResponse)
async def list_acronyms(
    prefix: Optional[str] = Query(None, description="Filter acronyms by prefix"),
    min_len: Optional[int] = Query(None, ge=1, description="Minimum acronym length"),
    max_len: Optional[int] = Query(None, ge=1, description="Maximum acronym length"),
    limit: int = Query(100, ge=1, le=1000, description="Page size"),
    offset: int = Query(0, ge=0, description="Offset into the acronym list"),
    store: ResolverStore = Depends(get_resolver_store),
):
    acronym_index = getattr(store, "acronym_index", {}) or {}
    entries = []
    for acronym, concepts in acronym_index.items():
        if prefix and not acronym.startswith(prefix.upper()):
            continue
        if min_len is not None and len(acronym) < min_len:
            continue
        if max_len is not None and len(acronym) > max_len:
            continue
        entries.append((acronym, concepts))

    entries.sort(key=lambda item: item[0])
    total = len(entries)
    sliced = entries[offset : offset + limit]
    items = [{"acronym": acronym, "concepts": concepts} for acronym, concepts in sliced]
    return {"total": total, "items": items}
