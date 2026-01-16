import os
import re
import string
from datetime import timedelta
from fastapi import FastAPI, Query, Request, Depends
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv
import mysql.connector

from contextlib import asynccontextmanager
from store import ResolverStore

# Load environment variables
load_dotenv()

STORE_REFRESH_TTL = os.getenv("STORE_REFRESH_TTL", 60)

# MySQL config
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASS"),
    "database": os.getenv("DB_NAME"),
    "port": int(os.getenv("DB_PORT", 3306)),
}

VIEW_NAME = os.getenv("OMOP_VIEW", "distribution_concepts")
DEFAULT_THRESHOLD = os.getenv("DEFAULT_THRESHOLD", 90)


# ------------------------------------------------------------
# Load OMOP concepts from MySQL
# ------------------------------------------------------------
def load_concepts_from_mysql():
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor(dictionary=True)
    cursor.execute(f"""
        SELECT
            concept_id,
            concept_name,
            description,
            domain_id,
            vocabulary_id,
            concept_class_id,
            standard_concept
        FROM {VIEW_NAME}
        WHERE
            concept_name IS NOT NULL
            OR description IS NOT NULL;
    """)
    concepts = cursor.fetchall()
    conn.close()
    return concepts


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = ResolverStore(load_concepts_from_mysql, ttl_seconds=STORE_REFRESH_TTL)
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


# ------------------------------------------------------------


def split_candidates(text: str) -> List[str]:
    """
    Split text into candidate phrases based on common clinical separators.
    """
    splitters = r", | and | with | who has | due to | because of |; "
    candidates = [
        s.strip() for s in re.split(splitters, text, flags=re.IGNORECASE) if s.strip()
    ]
    print(f"found candidates {candidates}")
    return candidates


def clean_candidates(text: str) -> str:
    text = text.translate(str.maketrans("", "", string.punctuation))
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def is_negated(text: str) -> bool:
    """
    Returns True if any negation term appears as a whole word in the text.
    """
    for term in NEGATION_TERMS:
        if re.search(rf"\b{re.escape(term)}\b", text, re.IGNORECASE):
            print(f"Negation term matched: '{term}' in '{text}'")
            return True
    return False


NEGATION_TERMS = {"no", "not", "without", "never"}
AGE_PATTERNS = [
    (re.compile(r"under\s+(\d+)", re.I), "<"),
    (re.compile(r"over\s+(\d+)", re.I), ">"),
    (re.compile(r"(\d+)\+", re.I), ">="),
    (re.compile(r"aged\s+(\d+)[--](\d+)", re.I), "range"),
]

UNSUPPORTED_PATTERNS = {
    "visit": re.compile(r"\b(visit|gp|hospital|admitted)\b", re.I),
    "sequence": re.compile(r"\b(after|before|later|followed by)\b", re.I),
    "location": re.compile(r"\b(regions|region|england|scotland|wales)\b", re.I),
    "measurement": re.compile(r"\b(above|below|mmol|value)\b", re.I),
}


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
    negated: bool = False


class QueryResponse(BaseModel):
    entities: List[Entity]
    warnings: List[str] = []


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
    candidates = split_candidates(payload.query)
    entities = []
    seen = set()
    entity_age_constraints = []
    warnings = []

    for candidate in candidates:
        candidate_clean = clean_candidates(candidate)

        # Negation
        negated = is_negated(candidate)

        print(f"Processing candidate: '{candidate}' (clean: '{candidate_clean}'), negated={negated}")

        # Age constraints
        age_constraints = []
        for pattern, op in AGE_PATTERNS:
            m = pattern.search(candidate)
            if m:
                age_constraints.append({"operator": op, "values": list(m.groups())})

        entity_age_constraints = age_constraints

        # Unsupported concepts
        unsupported = [
            name for name, pattern in UNSUPPORTED_PATTERNS.items() if pattern.search(candidate)
        ]

        for feature in unsupported:
            warnings.append(f"{feature.capitalize()}-based filtering is not currently supported.")

        # Resolve concepts
        matches = resolver.resolve(candidate_clean, threshold, phrase_first=phrase_first)
        index = payload.query.lower().find(candidate.lower())

        ## LS - Not needed, as this will default to creating a 'condition' based on the query context
        ## in the event of no matches. Causes some confusion. Left in for completeness.
        ##
        # if not matches:
        #     # No matches found, still record the candidate
        #     index = payload.query.lower().find(candidate.lower())
        #     start_idx = index
        #     end_idx = start_idx + len(candidate)

        #     entities.append({
        #         "text": candidate,
        #         "label": None,
        #         "start": start_idx,
        #         "end": end_idx,
        #         "negated": negated,
        #         "age_constraints": entity_age_constraints if entity_age_constraints is not None else [],
        #         "attributes": {},
        #     })
        #     continue

        for match in matches:
            key = (match["concept_id"], candidate.lower(), index)
            if key in seen:
                continue
            seen.add(key)

            start_idx = index
            end_idx = start_idx + len(candidate)

            entities.append({
                "text": candidate,
                "label": match.get("domain_id"),
                "start": start_idx,
                "end": end_idx,
                "negated": negated,
                "age_constraints": entity_age_constraints if entity_age_constraints is not None else [],
                "attributes": match,
            })

    return {
        "entities": entities,
        "warnings": warnings,
    }

@app.get("/")
def root():
    return {
        "message": "Cohort Discovery NLP Service running. POST to /extract with {query: 'your text'}"
    }
