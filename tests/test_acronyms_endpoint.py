from fastapi.testclient import TestClient

from app import app
from fuzzy_concept_resolver import FuzzyConceptResolver
from rules_engine import RuleEngine


class LocalResolverStore:
    def __init__(self, resolver):
        self._resolver = resolver

    async def get_resolver(self):
        return self._resolver


client = TestClient(app)
try:
    app.state.resolver_store
except AttributeError:
    app.state.resolver_store = LocalResolverStore(FuzzyConceptResolver([]))


def seed_acronym_resolver():
    concepts = [
        {
            "concept_id": 1,
            "concept_name": "Chronic obstructive pulmonary disease",
            "description": "Chronic obstructive pulmonary disease",
        },
        {
            "concept_id": 2,
            "concept_name": "Chronic kidney disease",
            "description": "Chronic kidney disease",
        },
        {
            "concept_id": 3,
            "concept_name": "Atrial fibrillation",
            "description": "Atrial fibrillation",
        },
    ]
    resolver = FuzzyConceptResolver(concepts)
    resolver.acronym_index = RuleEngine().build_acronym_index(concepts)
    return LocalResolverStore(resolver)


def test_acronyms_endpoint_returns_items():
    previous_store = app.state.resolver_store
    app.state.resolver_store = seed_acronym_resolver()
    try:
        response = client.get("/acronyms")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] >= 1
        acronyms = {entry["acronym"] for entry in body["items"]}
        assert "COPD" in acronyms
        assert "CKD" in acronyms
    finally:
        app.state.resolver_store = previous_store


def test_acronyms_endpoint_filters_by_prefix():
    previous_store = app.state.resolver_store
    app.state.resolver_store = seed_acronym_resolver()
    try:
        response = client.get("/acronyms?prefix=ck")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] >= 1
        assert all(entry["acronym"].startswith("CK") for entry in body["items"])
    finally:
        app.state.resolver_store = previous_store


def test_acronyms_endpoint_pagination():
    previous_store = app.state.resolver_store
    app.state.resolver_store = seed_acronym_resolver()
    try:
        response = client.get("/acronyms?limit=1&offset=0")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] >= 2
        assert len(body["items"]) == 1

        response_next = client.get("/acronyms?limit=1&offset=1")
        assert response_next.status_code == 200
        body_next = response_next.json()
        assert len(body_next["items"]) == 1
        assert body_next["items"][0]["acronym"] != body["items"][0]["acronym"]
    finally:
        app.state.resolver_store = previous_store


def test_acronyms_endpoint_search_prefix():
    previous_store = app.state.resolver_store
    app.state.resolver_store = seed_acronym_resolver()
    try:
        response = client.get("/acronyms?prefix=co")
        assert response.status_code == 200
        body = response.json()
        assert any(entry["acronym"] == "COPD" for entry in body["items"])
        assert all(entry["acronym"].startswith("CO") for entry in body["items"])
    finally:
        app.state.resolver_store = previous_store
