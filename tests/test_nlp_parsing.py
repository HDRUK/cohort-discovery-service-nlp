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
        and e["age_constraints"][0]["min"] == 24
        and e["age_constraints"][0]["max"] is None
        and e["age_constraints"][0]["inclusive"] is False
        and e["age_constraints"][0]["scope"] in {"query", "entity"}
        for e in body["entities"]
    )

    # Warnings
    assert body.get("warnings") == []

def test_fuzzy_token_overlap_handles_simple_misspelling(monkeypatch):
    monkeypatch.setenv("FUZZY_TOKEN_OVERLAP", "true")
    monkeypatch.setenv("FUZZY_TOKEN_MIN_SCORE", "90")

    previous_store = app.state.resolver_store
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

    try:
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
    finally:
        app.state.resolver_store = previous_store

def test_women_over_50_with_diabetes_age_constraint():
    response = client.post(
        "/extract?threshold=70",
        json={"query": "women over 50 diagnosed with diabetes"},
    )

    assert response.status_code == 200
    body = response.json()

    assert "entities" in body
    assert len(body["entities"]) >= 1

    assert any(
        e.get("age_constraints")
        and e["age_constraints"][0]["min"] == 50
        and e["age_constraints"][0]["max"] is None
        and e["age_constraints"][0]["inclusive"] is False
        and e["age_constraints"][0]["scope"] in {"query", "entity"}
        for e in body["entities"]
    )

def test_women_under_60_hip_fracture_entity_age_constraint():
    local_concepts = [
        {
            "concept_id": 10,
            "concept_name": "Hip fracture",
            "description": "Hip fracture",
            "domain_id": "Condition",
            "vocabulary_id": "SNOMED",
            "concept_class_id": "Disorder",
            "standard_concept": "S",
        },
        {
            "concept_id": 11,
            "concept_name": "Female",
            "description": "Female",
            "domain_id": "Gender",
            "vocabulary_id": "SNOMED",
            "concept_class_id": "Gender",
            "standard_concept": "S",
        },
    ]

    app.state.resolver_store = LocalResolverStore(FuzzyConceptResolver(local_concepts))

    response = client.post(
        "/extract?threshold=70",
        json={"query": "Women who were under 60 when they suffered a hip fracture"},
    )

    assert response.status_code == 200
    body = response.json()
    assert "entities" in body
    assert len(body["entities"]) >= 1

    assert any(
        e.get("attributes", {}).get("description", "").lower() == "hip fracture"
        for e in body["entities"]
    )
    assert any(
        e.get("attributes", {}).get("description", "").lower() == "female"
        for e in body["entities"]
    )

    assert all(
        e.get("age_constraints")
        and e["age_constraints"][0]["min"] is None
        and e["age_constraints"][0]["max"] == 60
        and e["age_constraints"][0]["inclusive"] is False
        and e["age_constraints"][0]["scope"] == "entity"
        for e in body["entities"]
    )

def test_adults_aged_18_30_with_diagnosis_of_asthma():
    local_concepts = [
        {
            "concept_id": 20,
            "concept_name": "Asthma",
            "description": "Asthma",
            "domain_id": "Condition",
            "vocabulary_id": "SNOMED",
            "concept_class_id": "Disorder",
            "standard_concept": "S",
        },
    ]

    previous_store = app.state.resolver_store
    app.state.resolver_store = LocalResolverStore(FuzzyConceptResolver(local_concepts))
    try:
        response = client.post(
            "/extract?threshold=70",
            json={"query": "Adults aged 18–30 with a diagnosis of asthma"},
        )

        assert response.status_code == 200
        body = response.json()
        assert "entities" in body
        assert len(body["entities"]) >= 1

        assert any(
            e.get("attributes", {}).get("description", "").lower() == "asthma"
            for e in body["entities"]
        )

        assert any(
            e.get("age_constraints")
            and e["age_constraints"][0]["min"] == 18
            and e["age_constraints"][0]["max"] == 30
            and e["age_constraints"][0]["inclusive"] is True
            and e["age_constraints"][0]["scope"] == "entity"
            for e in body["entities"]
        )
    finally:
        app.state.resolver_store = previous_store

def test_people_aged_65_plus_with_diagnosed_hypertension():
    local_concepts = [
        {
            "concept_id": 21,
            "concept_name": "Hypertension",
            "description": "Hypertension",
            "domain_id": "Condition",
            "vocabulary_id": "SNOMED",
            "concept_class_id": "Disorder",
            "standard_concept": "S",
        },
    ]

    previous_store = app.state.resolver_store
    app.state.resolver_store = LocalResolverStore(FuzzyConceptResolver(local_concepts))
    try:
        response = client.post(
            "/extract?threshold=70",
            json={"query": "People aged 65+ with diagnosed hypertension"},
        )

        assert response.status_code == 200
        body = response.json()
        assert "entities" in body
        assert len(body["entities"]) >= 1

        assert any(
            e.get("attributes", {}).get("description", "").lower() == "hypertension"
            for e in body["entities"]
        )

        assert any(
            e.get("age_constraints")
            and e["age_constraints"][0]["min"] == 65
            and e["age_constraints"][0]["max"] is None
            and e["age_constraints"][0]["inclusive"] is True
            and e["age_constraints"][0]["scope"] == "entity"
            for e in body["entities"]
        )
    finally:
        app.state.resolver_store = previous_store

def test_people_with_chronic_kidney_disease_stage_3_5():
    local_concepts = [
        {
            "concept_id": 22,
            "concept_name": "Chronic kidney disease stage 3-5",
            "description": "Chronic kidney disease stage 3-5",
            "domain_id": "Condition",
            "vocabulary_id": "SNOMED",
            "concept_class_id": "Disorder",
            "standard_concept": "S",
        },
    ]

    previous_store = app.state.resolver_store
    app.state.resolver_store = LocalResolverStore(FuzzyConceptResolver(local_concepts))
    try:
        response = client.post(
            "/extract?threshold=70",
            json={"query": "People with chronic kidney disease stage 3–5"},
        )

        assert response.status_code == 200
        body = response.json()
        assert "entities" in body
        assert len(body["entities"]) >= 1

        assert any(
            e.get("attributes", {}).get("description", "").lower()
            == "chronic kidney disease stage 3-5"
            for e in body["entities"]
        )
    finally:
        app.state.resolver_store = previous_store


def test_adults_with_diabetes_diagnosed_last_two_years_time_constraint():
    local_concepts = [
        {
            "concept_id": 30,
            "concept_name": "Type 2 diabetes mellitus",
            "description": "Type 2 diabetes mellitus",
            "domain_id": "Condition",
            "vocabulary_id": "SNOMED",
            "concept_class_id": "Disorder",
            "standard_concept": "S",
        },
    ]

    previous_store = app.state.resolver_store
    app.state.resolver_store = LocalResolverStore(FuzzyConceptResolver(local_concepts))
    try:
        response = client.post(
            "/extract?threshold=70",
            json={"query": "Adults with type 2 diabetes diagnosed in the last 2 years"},
        )

        assert response.status_code == 200
        body = response.json()
        assert "entities" in body
        assert len(body["entities"]) >= 1

        assert any(
            e.get("attributes", {}).get("description", "").lower() == "type 2 diabetes mellitus"
            for e in body["entities"]
        )

        assert any(
            e.get("time_constraints")
            and e["time_constraints"][0]["from"]
            and e["time_constraints"][0]["to"]
            and e["time_constraints"][0]["scope"] in {"query", "entity"}
            for e in body["entities"]
        )
    finally:
        app.state.resolver_store = previous_store


def test_adults_with_diabetes_does_not_create_demographic_entity():
    local_concepts = [
        {
            "concept_id": 30,
            "concept_name": "Type 2 diabetes mellitus",
            "description": "Type 2 diabetes mellitus",
            "domain_id": "Condition",
            "vocabulary_id": "SNOMED",
            "concept_class_id": "Disorder",
            "standard_concept": "S",
        },
    ]

    previous_store = app.state.resolver_store
    app.state.resolver_store = LocalResolverStore(FuzzyConceptResolver(local_concepts))
    try:
        response = client.post(
            "/extract?threshold=70",
            json={"query": "Adults with type 2 diabetes diagnosed in the last 2 years"},
        )

        assert response.status_code == 200
        body = response.json()
        assert "entities" in body
        assert len(body["entities"]) >= 1

        assert any(
            e.get("attributes", {}).get("description", "").lower() == "type 2 diabetes mellitus"
            for e in body["entities"]
        )

        assert all(e.get("text", "").lower() != "adults" for e in body["entities"])
    finally:
        app.state.resolver_store = previous_store


def test_children_with_asthma_default_age_constraint():
    local_concepts = [
        {
            "concept_id": 31,
            "concept_name": "Asthma",
            "description": "Asthma",
            "domain_id": "Condition",
            "vocabulary_id": "SNOMED",
            "concept_class_id": "Disorder",
            "standard_concept": "S",
        },
    ]

    previous_store = app.state.resolver_store
    app.state.resolver_store = LocalResolverStore(FuzzyConceptResolver(local_concepts))
    try:
        response = client.post(
            "/extract?threshold=70",
            json={"query": "Children with asthma"},
        )

        assert response.status_code == 200
        body = response.json()
        assert "entities" in body
        assert len(body["entities"]) >= 1

        assert any(
            e.get("attributes", {}).get("description", "").lower() == "asthma"
            for e in body["entities"]
        )

        assert any(
            e.get("age_constraints")
            and e["age_constraints"][0]["min"] == 0
            and e["age_constraints"][0]["max"] == 17
            and e["age_constraints"][0]["inclusive"] is True
            for e in body["entities"]
        )
    finally:
        app.state.resolver_store = previous_store


def test_elderly_with_heart_failure_default_age_constraint():
    local_concepts = [
        {
            "concept_id": 32,
            "concept_name": "Heart failure",
            "description": "Heart failure",
            "domain_id": "Condition",
            "vocabulary_id": "SNOMED",
            "concept_class_id": "Disorder",
            "standard_concept": "S",
        },
    ]

    previous_store = app.state.resolver_store
    app.state.resolver_store = LocalResolverStore(FuzzyConceptResolver(local_concepts))
    try:
        response = client.post(
            "/extract?threshold=70",
            json={"query": "Elderly with heart failure"},
        )

        assert response.status_code == 200
        body = response.json()
        assert "entities" in body
        assert len(body["entities"]) >= 1

        assert any(
            e.get("attributes", {}).get("description", "").lower() == "heart failure"
            for e in body["entities"]
        )

        assert any(
            e.get("age_constraints")
            and e["age_constraints"][0]["min"] == 65
            and e["age_constraints"][0]["max"] is None
            and e["age_constraints"][0]["inclusive"] is True
            for e in body["entities"]
        )
    finally:
        app.state.resolver_store = previous_store
