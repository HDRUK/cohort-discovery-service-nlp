import os
import re
import string
import time
import sys
from datetime import timedelta
from fastapi import FastAPI, Query, Request, Depends
from pydantic import BaseModel
from typing import List, Dict, Any, Optional, Tuple
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
    start_time = time.time()
    
    # Connection phase
    conn_start = time.time()
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor(dictionary=True)
    conn_time = time.time() - conn_start
    
    # Query execution phase
    query_start = time.time()
    cursor.execute(f"""
        SELECT
            DISTINCT(concept_id),
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
    print(f"\n[Profiling] load_concepts_from_mysql")
    print(f"  - Connection time: {conn_time*1000:.2f}ms")
    print(f"  - Query execution time: {query_time*1000:.2f}ms")
    print(f"  - Fetch time: {fetch_time*1000:.2f}ms")
    print(f"  - Total time: {total_time*1000:.2f}ms")
    print(f"  - Concepts loaded: {len(concepts)}")
    print(f"  - Estimated memory: {concepts_size / 1024 / 1024:.2f}MB")
    print(f"  - TTL: {STORE_REFRESH_TTL}s\n")
    
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
    splitters = (
        r", | and | with | who have | who has | who received | who have received | who has received |"
        r" who've | who has been | who have been | who were given | who got | patients who | people who |"
        r" due to | because of |; | when they | when he | when she | when patients | when people "
    )
    candidates = [
        s.strip() for s in re.split(splitters, text, flags=re.IGNORECASE) if s.strip()
    ]
    print(f"found candidates {candidates}")
    return candidates


def clean_candidates(text: str) -> str:
    # Preserve hyphens so tokens like "covid-19" remain intact.
    punctuation = string.punctuation.replace("-", "")
    text = text.translate(str.maketrans("", "", punctuation))
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def strip_leading_verbs(text: str) -> str:
    """
    Remove leading ingestion/administration verbs that dilute concept matching.
    """
    text = text.strip()
    # Only strip when the verb is at the very start to avoid over-splitting.
    patterns = [
        r"^(received|got|given|administered)\s+",
        r"^vaccinated\s+with\s+",
        r"^vaccination\s+with\s+",
        r"^(suffered|had|experienced)\s+",
        r"^diagnosed\s+with\s+",
        r"^diagnosed\s+",
        r"^(a|an|the)\s+diagnosis\s+of\s+",
        r"^(diagnosis|history)\s+of\s+",
        r"^(a|an|the)\s+",
    ]
    for pattern in patterns:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)
    return text.strip()

def apply_demographic_patterns(text: str) -> str:
    """
    Replace demographic wording with canonical tokens for matching.
    """
    for pattern, replacement in DEMOGRAPHC_PATTERNS:
        text = pattern.sub(replacement, text)
    return text.strip()

def extract_age_constraints(text: str, scope: str) -> Tuple[List[Dict[str, Any]], str]:
    """
    Extract normalised age constraints and strip them from the text.
    """
    constraints = []
    cleaned = text

    for pattern, op in AGE_PATTERNS:
        for m in pattern.finditer(text):
            if op == "<":
                max_age = int(m.group(1))
                constraints.append({"min": None, "max": max_age, "inclusive": False, "scope": scope})
            elif op == ">":
                min_age = int(m.group(1))
                constraints.append({"min": min_age, "max": None, "inclusive": False, "scope": scope})
            elif op == ">=":
                min_age = int(m.group(1))
                constraints.append({"min": min_age, "max": None, "inclusive": True, "scope": scope})
            elif op == "range":
                min_age = int(m.group(1))
                max_age = int(m.group(2))
                if min_age > max_age:
                    min_age, max_age = max_age, min_age
                constraints.append({"min": min_age, "max": max_age, "inclusive": True, "scope": scope})

        cleaned = pattern.sub("", cleaned)

    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return constraints, cleaned

def merge_age_constraints(primary: List[Dict[str, Any]], secondary: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Merge age constraints, avoiding duplicates.
    """
    merged = []
    seen = set()
    for entry in primary + secondary:
        key = (entry.get("min"), entry.get("max"), entry.get("inclusive"), entry.get("scope"))
        if key in seen:
            continue
        seen.add(key)
        merged.append(entry)
    return merged

def has_non_demographic_content(text: str) -> bool:
    """
    Returns True if text contains content beyond demographics/connector words.
    """
    text = re.sub(r"\b(MALE|FEMALE|CHILD)\b", "", text, flags=re.IGNORECASE)
    text = re.sub(
        r"\b(who|were|are|is|aged|age|under|over|when|they|he|she|people|patients|with|the|a|an)\b",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\s+", " ", text).strip()
    return bool(text)

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

DEMOGRAPHC_PATTERNS = [
    (re.compile(r"\bmales\b", re.I), "MALE"),
    (re.compile(r"\bmen\b", re.I), "MALE"),
    (re.compile(r"\bboys\b", re.I), "MALE"),
    (re.compile(r"\bwomen\b", re.I), "FEMALE"),
    (re.compile(r"\bfemales\b", re.I), "FEMALE"),
    (re.compile(r"\bgirls\b", re.I), "FEMALE"),
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
    global_age_constraints, _ = extract_age_constraints(payload.query, "query")
    entity_age_constraints_all: List[Dict[str, Any]] = []
    has_event_candidate = False

    # Pre-pass: detect whether any candidate includes non-demographic content
    for candidate in candidates:
        candidate_age_constraints, candidate_without_age = extract_age_constraints(candidate, "entity")
        candidate_clean = strip_leading_verbs(clean_candidates(candidate_without_age))
        candidate_normalised = apply_demographic_patterns(candidate_clean)
        if has_non_demographic_content(candidate_normalised):
            has_event_candidate = True
            break

    # Pre-pass: collect any age constraints found in candidate phrases
    for candidate in candidates:
        candidate_age_constraints, candidate_without_age = extract_age_constraints(candidate, "entity")
        candidate_clean = strip_leading_verbs(clean_candidates(candidate_without_age))
        candidate_normalised = apply_demographic_patterns(candidate_clean)

        if re.search(r"\bCHILD\b", candidate_normalised, re.IGNORECASE):
            if not candidate_age_constraints:
                candidate_age_constraints.append({"min": None, "max": 18, "inclusive": False, "scope": "entity"})

        if candidate_age_constraints:
            if not has_event_candidate:
                for constraint in candidate_age_constraints:
                    constraint["scope"] = "query"
            entity_age_constraints_all = merge_age_constraints(entity_age_constraints_all, candidate_age_constraints)

    for candidate in candidates:
        candidate_age_constraints, candidate_without_age = extract_age_constraints(candidate, "entity")
        candidate_clean = strip_leading_verbs(clean_candidates(candidate_without_age))
        candidate_normalised = apply_demographic_patterns(candidate_clean)

        # Negation
        negated = is_negated(candidate)

        print(
            f"Processing candidate: '{candidate}' (clean: '{candidate_clean}', normalised: '{candidate_normalised}'), negated={negated}"
        )

        # Age constraints
        if re.search(r"\bCHILD\b", candidate_normalised, re.IGNORECASE):
            if not candidate_age_constraints:
                candidate_age_constraints.append({"min": None, "max": 18, "inclusive": False, "scope": "entity"})
            candidate_normalised = re.sub(r"\bCHILD\b", "", candidate_normalised, flags=re.IGNORECASE)
            candidate_normalised = re.sub(r"\s+", " ", candidate_normalised).strip()

        if entity_age_constraints_all:
            entity_age_constraints = entity_age_constraints_all
        else:
            entity_age_constraints = merge_age_constraints(global_age_constraints, candidate_age_constraints)

        # Unsupported concepts
        unsupported = [
            name for name, pattern in UNSUPPORTED_PATTERNS.items() if pattern.search(candidate)
        ]

        for feature in unsupported:
            warnings.append(f"{feature.capitalize()}-based filtering is not currently supported.")

        # Resolve concepts
        matches = resolver.resolve(candidate_normalised, threshold, phrase_first=phrase_first)
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

    response = {
        "entities": entities,
        "warnings": warnings,
    }
    print(f"Response: {response}")

    return response

@app.get("/")
def root():
    return {
        "message": "Cohort Discovery NLP Service running. POST to /extract with {query: 'your text'}"
    }
