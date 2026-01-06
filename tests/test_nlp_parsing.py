from fastapi.testclient import TestClient
from app import app, load_concepts_from_mysql
from fuzzy_concept_resolver import FuzzyConceptResolver
from state_setup import ResolverStore

app.state.resolver_store = ResolverStore()

client = TestClient(app)

def test_adults_type2_diabetes_last_2_years():
    response = client.post(
        "/extract",
        json={"query":  "Adults over 24 with type 2 diabetes diagnosed in the last 2 years"},
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
    for e in body["entities"]:
        assert e.get("age_constraints") is not None
        if e["age_constraints"]:
            assert e["age_constraints"][0]["operator"] == ">"
            assert e["age_constraints"][0]["values"] == ["24"]

    # Unsupported
    assert entity["unsupported"] == []