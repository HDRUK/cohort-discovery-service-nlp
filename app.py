import os
import sys
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

import mysql.connector
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Query, Request
from pydantic import BaseModel

from parsing import QueryParser
from rules_engine import RuleEngine
from store import ResolverStore


# Load environment variables
load_dotenv()

STORE_REFRESH_TTL = int(os.getenv("STORE_REFRESH_TTL", 60))

# MySQL config
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASS"),
    "database": os.getenv("DB_NAME"),
    "port": int(os.getenv("DB_PORT", 3306)),
}

VIEW_NAME = os.getenv("OMOP_VIEW", "distribution_concepts")
DEFAULT_THRESHOLD = int(os.getenv("DEFAULT_THRESHOLD", 90))


# ------------------------------------------------------------
# Load OMOP concepts from MySQL
# ------------------------------------------------------------
def load_concepts_from_mysql():
    start_time = time.time()

    # Connection phase
    conn_start = time.time()
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor(dictionary=True)
    conn_time = time.time() - conn_start

    # Query execution phase
    query_start = time.time()
    cursor.execute(
        f"""
        SELECT
            concept_id,
            concept_name,
            concept_name as description,
            domain_id,
            vocabulary_id,
            concept_class,
            standard_concept,
            concept_code,
            count,
            ncollections,
            all_synthetic
        FROM {VIEW_NAME}
        WHERE concept_name IS NOT NULL;
        """
    )
    query_time = time.time() - query_start

    # Fetch phase
    fetch_start = time.time()
    concepts = cursor.fetchall()
    fetch_time = time.time() - fetch_start

    conn.close()

    total_time = time.time() - start_time

    # Estimate memory usage (rough approximation)
    concepts_size = sys.getsizeof(concepts)

    # Print profiling information
    print("\n[Profiling] load_concepts_from_mysql")
    print(f"  - Connection time: {conn_time * 1000:.2f}ms")
    print(f"  - Query execution time: {query_time * 1000:.2f}ms")
    print(f"  - Fetch time: {fetch_time * 1000:.2f}ms")
    print(f"  - Total time: {total_time * 1000:.2f}ms")
    print(f"  - Concepts loaded: {len(concepts)}")
    print(f"  - Estimated memory: {concepts_size / 1024 / 1024:.2f}MB")
    print(f"  - TTL: {STORE_REFRESH_TTL}s\n")

    return concepts


def enrich_resolver(resolver, concepts):
    resolver.acronym_index = ENGINE.build_acronym_index(concepts)


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = ResolverStore(
        load_concepts_from_mysql,
        ttl_seconds=STORE_REFRESH_TTL,
        postprocess=enrich_resolver,
    )
    resolver = await store.get_resolver()
    app.state.resolver_store = store
    print(
        f"[Start-up] Loaded FuzzyConceptResolver (concepts={len(resolver.concepts)}) from `{VIEW_NAME}`"
    )
    yield


def get_resolver_store(request: Request) -> ResolverStore:
    return request.app.state.resolver_store


# FastAPI app
app = FastAPI(title="Project Daphne NLP Service", version="1.0", lifespan=lifespan)

# Parsing engine
ENGINE = RuleEngine()
PARSER = QueryParser(ENGINE)


# ------------------------------------------------------------
# Pydantic models
# ------------------------------------------------------------
class QueryRequest(BaseModel):
    query: str


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


class QueryResponse(BaseModel):
    entities: List[Entity]
    groups: List[Group] = []
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
    threshold: float = Query(
        DEFAULT_THRESHOLD, description="Fuzzy match threshold 0-100"
    ),
    phrase_first: bool = Query(
        True, description="Prefer phrase overlap when token matching is available"
    ),
    store: ResolverStore = Depends(get_resolver_store),
):
    """
    Extract clinical concepts from query using fuzzy matching.
    """
    resolver = await store.get_resolver()
    ret_value = PARSER.extract(payload.query, threshold, phrase_first, resolver)

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
    resolver = await store.get_resolver()
    acronym_index = getattr(resolver, "acronym_index", {}) or {}
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
