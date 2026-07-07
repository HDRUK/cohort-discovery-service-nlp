import asyncio
import os
import sys
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Query, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.engine import URL as EngineURL

from concepts import router as concepts_router
from loaders.ancestors import load_ancestor_map
from loaders.concepts import load_concepts_from_mysql
from loaders.synonyms import build_synonym_token_index, load_synonym_map
from resolvers.sql_helpers import build_concepts_by_id, build_name_token_index
from parsing import QueryParser
from resolvers import MedCATClient, MySQLConceptResolver
from rules_engine import RuleEngine
from store import ResolverStore
from logging_config import get_logger

log = get_logger()


def _deep_sizeof(obj: Any) -> int:
    """Approximate the total in-memory size (bytes) of a nested container,
    recursing through dicts/lists/tuples/sets/frozensets and counting each
    distinct object once."""
    seen: set[int] = set()
    stack = [obj]
    total = 0
    while stack:
        item = stack.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        total += sys.getsizeof(item)
        if isinstance(item, dict):
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, (list, tuple, set, frozenset)):
            stack.extend(item)
    return total


def _fmt_bytes(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f}{unit}"
        size /= 1024


# Load environment variables
load_dotenv()

STORE_REFRESH_TTL = int(os.getenv("STORE_REFRESH_TTL", 60))
RESOLVER_BACKEND = os.getenv("RESOLVER_BACKEND", "sql")
APP_ENV = os.getenv("APP_ENV", "production").lower()

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
        s0 = time.monotonic()
        store.synonym_map = load_synonym_map(
            OMOP_DB_CONFIG, [c["concept_id"] for c in concepts]
        )
        s1 = time.monotonic()
        log.info(
            f"[warmup] postprocess.load_synonym_map: {s1 - s0:.2f}s ({len(store.synonym_map)} concepts, {_fmt_bytes(_deep_sizeof(store.synonym_map))})"
        )
        store.synonym_token_index = build_synonym_token_index(store.synonym_map)
        s2 = time.monotonic()
        log.info(
            f"[warmup] postprocess.build_synonym_token_index: {s2 - s1:.2f}s ({len(store.synonym_token_index)} tokens, {_fmt_bytes(_deep_sizeof(store.synonym_token_index))})"
        )
        store.acronym_index = ENGINE.build_acronym_index(concepts)
        s3 = time.monotonic()
        log.info(
            f"[warmup] postprocess.build_acronym_index: {s3 - s2:.2f}s ({len(store.acronym_index)} acronyms, {_fmt_bytes(_deep_sizeof(store.acronym_index))})"
        )
        store.name_token_index = build_name_token_index(concepts)
        s4 = time.monotonic()
        log.info(
            f"[warmup] postprocess.build_name_token_index: {s4 - s3:.2f}s ({len(store.name_token_index)} tokens, {_fmt_bytes(_deep_sizeof(store.name_token_index))})"
        )
        store.ancestor_map = load_ancestor_map(
            OMOP_DB_CONFIG, [c["concept_id"] for c in concepts]
        )
        s5 = time.monotonic()
        log.info(
            f"[warmup] postprocess.load_ancestor_map: {s5 - s4:.2f}s ({len(store.ancestor_map)} parents, {_fmt_bytes(_deep_sizeof(store.ancestor_map))})"
        )
        store.concepts_by_id = build_concepts_by_id(concepts)
        s6 = time.monotonic()
        log.info(
            f"[warmup] postprocess.build_concepts_by_id: {s6 - s5:.2f}s ({len(store.concepts_by_id)} concepts, {_fmt_bytes(_deep_sizeof(store.concepts_by_id))})"
        )
        total_bytes = sum(
            _deep_sizeof(m)
            for m in (
                store.synonym_map,
                store.synonym_token_index,
                store.acronym_index,
                store.name_token_index,
                store.ancestor_map,
                store.concepts_by_id,
            )
        )
        log.info(f"[warmup] postprocess: total lookup size {_fmt_bytes(total_bytes)}")

    # Concepts are loaded at startup for three reasons:
    #   1. FuzzyConceptResolver — iterates the full list on every resolve (RESOLVER_BACKEND=fuzzy).
    #   2. synonym_map — built from concept IDs; also used by MySQLConceptResolver at request time.
    #   3. acronym_index — built from concept names; used by QueryParser for acronym expansion.
    #   4. concepts_by_id — id -> {name, category}; used to hydrate child concepts (ancestors).
    # If RESOLVER_BACKEND=sql is permanent and the fuzzy fallback in FallbackResolver is removed,
    # this load could be replaced by a lighter synonym-only query. Refactor candidate.
    # Dev-only warm-up snapshot cache: uvicorn --reload restores the tokenised concepts +
    # synonym/acronym maps from disk instead of re-querying MySQL on every save. Never in prod.
    cache_path = (
        os.getenv("CONCEPTS_CACHE_PATH", ".concepts_cache.pkl")
        if APP_ENV == "development"
        else None
    )
    store = ResolverStore(
        lambda: load_concepts_from_mysql(db_engine),
        ttl_seconds=STORE_REFRESH_TTL,
        postprocess=enrich_resolver,
        cache_path=cache_path,
    )
    app.state.resolver_store = store

    medcat_url = os.getenv("MEDCAT_URL", "").strip()
    medcat_client = (
        MedCATClient(
            url=medcat_url,
            min_acc=float(os.getenv("MEDCAT_MIN_ACC", "0.5")),
        )
        if medcat_url
        else None
    )
    app.state.medcat_client = medcat_client

    app.state.sql_resolver = MySQLConceptResolver(db_engine, store, medcat_client)
    app.state.backend = RESOLVER_BACKEND

    # The server starts serving immediately in every environment. The concept/synonym/acronym
    # warm-up (and subsequent TTL refreshes) run in the background: until it completes, the
    # resolvers degrade gracefully (LIKE-based SQL search, no synonym boost / acronym expansion)
    # so requests are never blocked waiting for the load.
    log.info("[warmup] startup: serving now (reduced mode until warm-up completes)")
    refresh_task = asyncio.create_task(store.run_periodic_refresh())

    try:
        yield
    finally:
        if refresh_task:
            refresh_task.cancel()


def get_resolver_store(request: Request) -> ResolverStore:
    return request.app.state.resolver_store


# FastAPI app
app = FastAPI(title="Project Daphne NLP Service", version="1.0", lifespan=lifespan)
app.include_router(concepts_router)


@app.middleware("http")
async def log_request_time(request: Request, call_next):
    t0 = time.monotonic()
    response = await call_next(request)
    log.info(
        f"[Request] {request.method} {request.url.path} {response.status_code} {(time.monotonic() - t0) * 1000:.1f}ms"
    )
    return response


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
    use_collection_score: bool = True
    use_synonym_lookup: bool = True
    use_medcat: bool = True


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
def extract_entities(
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

    Sync endpoint (runs in FastAPI's threadpool) so the synchronous DB work never blocks the
    event loop. Resolver selection is non-blocking: until the background warm-up completes the
    request is served in reduced mode (LIKE-based SQL search, no synonym/acronym enrichment).
    """
    log.debug(
        f"[/extract] query='{payload.query}' threshold={threshold} "
        f"phrase_first={phrase_first} backend={RESOLVER_BACKEND}"
    )

    if store.resolver is None:
        log.info("[warmup] /extract in reduced mode (concepts still loading)")

    if RESOLVER_BACKEND == "fuzzy":
        # Non-blocking: use the fuzzy resolver if ready, else use SQL until warm-up completes.
        resolver = store.resolver or request.app.state.sql_resolver
    else:
        resolver = request.app.state.sql_resolver

    ret_value = PARSER.extract(
        payload.query,
        threshold,
        phrase_first,
        resolver,
        max_matches=max_matches,
        use_collection_filter=payload.use_collection_filter,
        collection_ids=list(payload.collection_ids) if payload.collection_ids else [],
        use_collection_score=payload.use_collection_score,
        use_synonym_lookup=payload.use_synonym_lookup,
        use_medcat=payload.use_medcat,
    )

    entities = ret_value.get("entities", [])
    warnings = ret_value.get("warnings", [])
    entity_names = [e.get("text", "") for e in entities]
    log.info(
        f"[/extract] query='{payload.query}' → {len(entities)} entities: {entity_names}"
    )
    if warnings:
        log.warning(f"[/extract] {len(warnings)} warning(s): {warnings}")

    return ret_value


@app.get("/")
def root():
    return {
        "message": "Cohort Discovery NLP Service running. POST to /extract with {query: 'your text'}"
    }


@app.get("/health/ready")
def readiness(response: Response, store: ResolverStore = Depends(get_resolver_store)):
    # store.resolver is set only at the end of warm-up, after every index is populated,
    # so it is an exact "fully warm" signal for both resolver backends. 503 keeps traffic
    # off the pod (via the k8s readinessProbe) until warm-up completes; 200 once ready.
    if store.resolver is None:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "warming"}
    return {"status": "ready"}


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
