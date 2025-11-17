import os
import re
import string
from fastapi import FastAPI, Query
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv
import mysql.connector

from fuzzy_concept_resolver import FuzzyConceptResolver

# Load environment variables
load_dotenv()

# MySQL config
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASS"),
    "database": os.getenv("DB_NAME"),
    "port": int(os.getenv("DB_PORT", 3306)),
}

VIEW_NAME = os.getenv("OMOP_VIEW", "distribution_concepts")

# FastAPI app
app = FastAPI(title="Project Daphne NLP Service", version="1.0")

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

concepts = load_concepts_from_mysql()
print(f"[Start-up] Loaded {len(concepts)} OMOP concepts from `{VIEW_NAME}`")

# ------------------------------------------------------------

def split_candidates(text: str) -> List[str]:
    """
    Split text into candidate phrases based on common clinical separators.
    """
    splitters = r",| and | with | who has | due to | because of |; "
    candidates = [s.strip() for s in re.split(splitters, text, flags=re.IGNORECASE) if s.strip()]
    print(f"found candidates {candidates}")
    return candidates

def clean_candidates(text: str) -> str:
    text = text.translate(str.maketrans("", "", string.punctuation))
    text = re.sub(r"\s+", " ", text)
    return text.strip()

# ------------------------------------------------------------
fuzzy_resolver = FuzzyConceptResolver(concepts)
print(f"[Start-up] Loaded FuzzyConceptResolver with default threshold {fuzzy_resolver.threshold}")

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

class QueryResponse(BaseModel):
    entities: List[Entity]

# ------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------
@app.post("/extract", response_model=QueryResponse)
def extract_entities(payload: QueryRequest, threshold: float = Query(80, description="Fuzzy match threshold 0-100")):
    """
    Extract clinical concepts from query using fuzzy matching.
    """
    candidates = split_candidates(payload.query)
    entities = []
    seen = set()

    for candidate in candidates:
        candidate_clean = clean_candidates(candidate)
        matches = fuzzy_resolver.resolve(candidate, threshold)
        for match in matches:
            key = (match["concept_id"], candidate.lower(), payload.query.lower().find(candidate.lower()))
            if key in seen:
                continue
            
            seen.add(key)
            start_idx = payload.query.lower().find(candidate.lower())
            end_idx = start_idx + len(candidate)
            entities.append({
                "text": candidate,
                "label": match["domain_id"],
                "start": start_idx,
                "end": end_idx,
                "attributes": match
            })

    return {"entities": entities}

@app.get("/")
def root():
    return {
        "message": "Cohort Discovery NLP Service running. POST to /extract with {query: 'your text'}"
    }
