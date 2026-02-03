import json
import os
import re
import string
import time
import sys
from datetime import datetime, timedelta
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
    splitters = RULES["splitters"]
    pattern = "|".join(splitters)
    candidates = [s.strip() for s in re.split(pattern, text, flags=re.IGNORECASE) if s.strip()]
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
    for pattern in RULES["leading_verbs"]:
        text = pattern.sub("", text)
    return text.strip()

def load_mappings():
    mappings_path = os.getenv("MAPPINGS_PATH", "mappings.json")
    try:
        with open(mappings_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        print(f"[Config] mappings file not found: {mappings_path}")
        sys.exit(1)
    except json.JSONDecodeError as exc:
        print(f"[Config] invalid mappings JSON in {mappings_path}: {exc}")
        sys.exit(1)

    compiled = {}
    for entry in data.get("mappings", []):
        group = entry.get("group", "default")
        compiled.setdefault(group, [])
        compiled[group].append(
            {
                "pattern": re.compile(entry["pattern"], re.IGNORECASE),
                "replacement": entry.get("replacement", ""),
                "warning": entry.get("warning"),
                "contains": entry.get("contains", []),
            }
        )
    return compiled


def load_rules():
    rules_path = os.getenv("RULES_PATH", "rules.json")
    try:
        with open(rules_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        print(f"[Config] rules file not found: {rules_path}")
        sys.exit(1)
    except json.JSONDecodeError as exc:
        print(f"[Config] invalid rules JSON in {rules_path}: {exc}")
        sys.exit(1)

    compiled = {
        "splitters": data.get("splitters", []),
        "leading_verbs": [re.compile(p, re.IGNORECASE) for p in data.get("leading_verbs", [])],
        "age_patterns": [
            (re.compile(entry["pattern"], re.IGNORECASE), entry["op"])
            for entry in data.get("age_patterns", [])
        ],
        "age_overrides": [
            {
                "pattern": re.compile(entry["pattern"], re.IGNORECASE),
                "min": entry.get("min"),
                "max": entry.get("max"),
                "inclusive": entry.get("inclusive", True),
            }
            for entry in data.get("age_overrides", [])
        ],
        "time_patterns": [
            (re.compile(entry["pattern"], re.IGNORECASE), entry["op"])
            for entry in data.get("time_patterns", [])
        ],
        "demographic_age_defaults": data.get("demographic_age_defaults", {}),
        "unsupported_patterns": {
            name: re.compile(pattern, re.IGNORECASE)
            for name, pattern in data.get("unsupported_patterns", {}).items()
        },
    }
    return compiled


# Pre-compile mappings and rules and cache
#
# This happens only at start-up to avoid lag in operation.
# If mappings/rules are updated, the service must be restarted.
#
MAPPINGS = load_mappings()
RULES = load_rules()


def apply_mappings(text: str, group: str, warnings: Optional[List[str]] = None) -> str:
    """
    Apply mapping rules for a group to the text.
    """
    for entry in MAPPINGS.get(group, []):
        if entry["contains"]:
            haystack = text.lower()
            if not any(token.lower() in haystack for token in entry["contains"]):
                continue
        if entry["pattern"].search(text):
            text = entry["pattern"].sub(entry["replacement"], text)
            if warnings is not None and entry.get("warning"):
                warnings.append(entry["warning"])
    return text.strip()

def apply_demographic_patterns(text: str) -> str:
    """
    Replace demographic wording with canonical tokens for matching.
    """
    return apply_mappings(text, "demographic")

def extract_age_constraints(text: str, scope: str) -> Tuple[List[Dict[str, Any]], str]:
    """
    Extract normalised age constraints and strip them from the text.
    """
    constraints = []
    cleaned = text

    for entry in AGE_OVERRIDES:
        for m in entry["pattern"].finditer(cleaned):
            constraints.append(
                {
                    "min": entry.get("min"),
                    "max": entry.get("max"),
                    "inclusive": entry.get("inclusive", True),
                    "scope": scope,
                }
            )
        cleaned = entry["pattern"].sub("", cleaned)

    for pattern, op in AGE_PATTERNS:
        for m in pattern.finditer(cleaned):
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


def extract_time_constraints(text: str, scope: str) -> Tuple[List[Dict[str, Any]], str]:
    """
    Extract normalized time constraints and strip them from the text.
    """
    constraints = []
    cleaned = text

    for pattern, op in TIME_PATTERNS:
        for m in pattern.finditer(cleaned):
            if op == "last":
                value = int(m.group(1))
                unit = m.group(2).lower()
                to_date = datetime.utcnow()
                if unit.startswith("year"):
                    from_date = to_date - timedelta(days=365 * value)
                else:
                    from_date = to_date - timedelta(days=30 * value)
                constraints.append(
                    {
                        "from": from_date.isoformat(),
                        "to": to_date.isoformat(),
                        "scope": scope,
                    }
                )
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


def merge_time_constraints(primary: List[Dict[str, Any]], secondary: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Merge time constraints, avoiding duplicates.
    """
    merged = []
    seen = set()
    for entry in primary + secondary:
        key = (entry.get("from"), entry.get("to"), entry.get("scope"))
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
    for term in DEMOGRAPHIC_AGE_DEFAULTS.keys():
        text = re.sub(rf"\b{re.escape(term)}s?\b", "", text, flags=re.IGNORECASE)
    text = re.sub(
        r"\b(who|were|are|is|aged|age|under|over|when|they|he|she|people|patients|with|the|a|an)\b",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\s+", " ", text).strip()
    return bool(text)


def find_demographic_age_default(text: str) -> Optional[Dict[str, Any]]:
    for term, defaults in DEMOGRAPHIC_AGE_DEFAULTS.items():
        if re.search(rf"\b{re.escape(term)}s?\b", text, re.IGNORECASE):
            return defaults
    return None

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
AGE_PATTERNS = RULES["age_patterns"]
AGE_OVERRIDES = RULES["age_overrides"]
TIME_PATTERNS = RULES["time_patterns"]
DEMOGRAPHIC_AGE_DEFAULTS = RULES["demographic_age_defaults"]
DEMOGRAPHC_PATTERNS = []
UNSUPPORTED_PATTERNS = RULES["unsupported_patterns"]


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
    entity_time_constraints = []
    warnings = []
    global_age_constraints, _ = extract_age_constraints(payload.query, "query")
    global_time_constraints, _ = extract_time_constraints(payload.query, "query")
    entity_age_constraints_all: List[Dict[str, Any]] = []
    entity_time_constraints_all: List[Dict[str, Any]] = []
    has_event_candidate = False

    # Pre-pass: detect whether any candidate includes non-demographic content
    for candidate in candidates:
        candidate_age_constraints, candidate_without_age = extract_age_constraints(candidate, "entity")
        candidate_time_constraints, candidate_without_time = extract_time_constraints(candidate_without_age, "entity")
        candidate_clean = strip_leading_verbs(clean_candidates(candidate_without_time))
        candidate_normalised = apply_demographic_patterns(candidate_clean)
        candidate_normalised = apply_mappings(candidate_normalised, "bmi", warnings)
        if has_non_demographic_content(candidate_normalised):
            has_event_candidate = True
            break

    # Pre-pass: collect any age constraints found in candidate phrases
    for candidate in candidates:
        candidate_age_constraints, candidate_without_age = extract_age_constraints(candidate, "entity")
        candidate_time_constraints, candidate_without_time = extract_time_constraints(candidate_without_age, "entity")
        candidate_clean = strip_leading_verbs(clean_candidates(candidate_without_time))
        candidate_normalised = apply_demographic_patterns(candidate_clean)
        candidate_normalised = apply_mappings(candidate_normalised, "bmi", warnings)

        if re.search(r"\bCHILD\b", candidate_normalised, re.IGNORECASE):
            if not candidate_age_constraints:
                candidate_age_constraints.append({"min": None, "max": 18, "inclusive": False, "scope": "entity"})

        if not candidate_age_constraints:
            defaults = find_demographic_age_default(candidate)
            if defaults:
                candidate_age_constraints.append(
                    {
                        "min": defaults.get("min"),
                        "max": defaults.get("max"),
                        "inclusive": defaults.get("inclusive", True),
                        "scope": "entity",
                    }
                )

        if candidate_age_constraints:
            if not has_event_candidate:
                for constraint in candidate_age_constraints:
                    constraint["scope"] = "query"
            entity_age_constraints_all = merge_age_constraints(entity_age_constraints_all, candidate_age_constraints)

        if candidate_time_constraints:
            if not has_event_candidate:
                for constraint in candidate_time_constraints:
                    constraint["scope"] = "query"
            entity_time_constraints_all = merge_time_constraints(
                entity_time_constraints_all, candidate_time_constraints
            )

    for candidate in candidates:
        candidate_age_constraints, candidate_without_age = extract_age_constraints(candidate, "entity")
        candidate_time_constraints, candidate_without_time = extract_time_constraints(candidate_without_age, "entity")
        candidate_clean = strip_leading_verbs(clean_candidates(candidate_without_time))
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

        if not candidate_age_constraints:
            defaults = find_demographic_age_default(candidate)
            if defaults:
                candidate_age_constraints.append(
                    {
                        "min": defaults.get("min"),
                        "max": defaults.get("max"),
                        "inclusive": defaults.get("inclusive", True),
                        "scope": "entity",
                    }
                )

        if entity_age_constraints_all:
            entity_age_constraints = entity_age_constraints_all
        else:
            entity_age_constraints = merge_age_constraints(global_age_constraints, candidate_age_constraints)

        if entity_time_constraints_all:
            entity_time_constraints = entity_time_constraints_all
        else:
            entity_time_constraints = merge_time_constraints(global_time_constraints, candidate_time_constraints)

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
                "time_constraints": entity_time_constraints if entity_time_constraints is not None else [],
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
