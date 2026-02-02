from fastapi.testclient import TestClient
from app import app
from fuzzy_concept_resolver import FuzzyConceptResolver


class LocalResolverStore:
    def __init__(self, resolver):
        self._resolver = resolver

    async def get_resolver(self):
        return self._resolver


concepts = [
    {
        "concept_id": 1,
        "concept_name": "Type 2 diabetes mellitus",
        "description": "Type 2 diabetes mellitus",
        "domain_id": "Condition",
        "vocabulary_id": "SNOMED",
        "concept_class_id": "Clinical Finding",
        "standard_concept": "S",
    }
]

app.state.resolver_store = LocalResolverStore(FuzzyConceptResolver(concepts))

client = TestClient(app)

def test_adults_type2_diabetes_last_2_years():
    response = client.post(
        "/extract?threshold=70",
        json={"query": "Type 2 diabetes over 24"},
    )

    assert response.status_code == 200
    body = response.json()

    assert "entities" in body
    assert len(body["entities"]) >= 1

    # entity = body["entities"][0]
    entity = next(
        (e for e in body["entities"] if e["attributes"].get("description")),
        body["entities"][0],
    )

    # Concept
    concept = entity["attributes"].get("description")
    if concept:
        assert "type 2 diabetes mellitus" in concept.lower()

    # Negation
    negated = entity.get("negated", False)
    assert negated is False

    # Age
    assert any(
        e.get("age_constraints")
        and e["age_constraints"][0]["operator"] == ">"
        and e["age_constraints"][0]["values"] == ["24"]
        for e in body["entities"]
    )

    # Warnings
    assert body.get("warnings") == []

def test_fuzzy_token_overlap_handles_simple_misspelling(monkeypatch):
    monkeypatch.setenv("FUZZY_TOKEN_OVERLAP", "true")
    monkeypatch.setenv("FUZZY_TOKEN_MIN_SCORE", "90")

    local_concepts = [
        {
            "concept_id": 2,
            "concept_name": "Asthma",
            "description": "Asthma",
            "domain_id": "Condition",
            "vocabulary_id": "SNOMED",
            "concept_class_id": "Clinical Finding",
            "standard_concept": "S",
        }
    ]

    app.state.resolver_store = LocalResolverStore(FuzzyConceptResolver(local_concepts))

    response = client.post(
        "/extract?threshold=70",
        json={"query": "astma"},
    )

    assert response.status_code == 200
    body = response.json()
    assert "entities" in body
    assert len(body["entities"]) >= 1

    entity = next(
        (e for e in body["entities"] if e["attributes"].get("description")),
        body["entities"][0],
    )
    concept = entity["attributes"].get("description")
    assert concept and "asthma" in concept.lower()
