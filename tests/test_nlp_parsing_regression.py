from fastapi.testclient import TestClient
import importlib

from app import app
from fuzzy_concept_resolver import FuzzyConceptResolver
import rules_engine


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


def has_age_constraint(body, min_age, max_age, inclusive, scope=None):
    constraints = list(body.get("age_constraints", []))
    for entity in body.get("entities", []):
        constraints.extend(entity.get("age_constraints", []))
    for constraint in constraints:
        if scope is not None and constraint.get("scope") != scope:
            continue
        if (
            constraint.get("min") == min_age
            and constraint.get("max") == max_age
            and constraint.get("inclusive") is inclusive
        ):
            return True
    return False


def test_cancer_and_diabetes_with_age_constraint():
    local_concepts = [
        {
            "concept_id": 36684857,
            "concept_name": "Cancer",
            "description": "Cancer",
            "domain_id": "Condition",
            "vocabulary_id": "SNOMED",
            "concept_class_id": "Disorder",
            "standard_concept": "S",
        },
        {
            "concept_id": 201826,
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
            json={"query": "patients who have been diagnosed with cancer and have diabetes aged over 40"},
        )

        assert response.status_code == 200
        body = response.json()
        assert "entities" in body
        assert len(body["entities"]) >= 1

        assert any(
            e.get("attributes", {}).get("description", "").lower() == "cancer"
            for e in body["entities"]
        )
        assert any(
            e.get("attributes", {}).get("description", "").lower() == "type 2 diabetes mellitus"
            for e in body["entities"]
        )

        assert has_age_constraint(body, 40, None, False)
    finally:
        app.state.resolver_store = previous_store


def test_cancer_over_50_treated_for_hip_fractures():
    local_concepts = [
        {
            "concept_id": 36684857,
            "concept_name": "Cancer",
            "description": "Cancer",
            "domain_id": "Condition",
            "vocabulary_id": "SNOMED",
            "concept_class_id": "Disorder",
            "standard_concept": "S",
        },
        {
            "concept_id": 10,
            "concept_name": "Hip fracture",
            "description": "Hip fracture",
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
            json={
                "query": "People with cancer over the age of 50 who have been treated for hip fractures"
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert "entities" in body
        assert len(body["entities"]) >= 1

        assert any(
            e.get("attributes", {}).get("description", "").lower() == "cancer"
            for e in body["entities"]
        )
        assert any(
            e.get("attributes", {}).get("description", "").lower() == "hip fracture"
            for e in body["entities"]
        )

        assert has_age_constraint(body, 50, None, False, scope="query")
    finally:
        app.state.resolver_store = previous_store


def test_location_warning_and_cancer_kept_for_nhs_scotland_query():
    local_concepts = [
        {
            "concept_id": 36684857,
            "concept_name": "Cancer",
            "description": "Cancer",
            "domain_id": "Condition",
            "vocabulary_id": "SNOMED",
            "concept_class_id": "Disorder",
            "standard_concept": "S",
        },
        {
            "concept_id": 8507,
            "concept_name": "MALE",
            "description": "MALE",
            "domain_id": "Gender",
            "vocabulary_id": "SNOMED",
            "concept_class_id": "Gender",
            "standard_concept": "S",
        },
    ]

    previous_store = app.state.resolver_store
    app.state.resolver_store = LocalResolverStore(FuzzyConceptResolver(local_concepts))
    try:
        response = client.post(
            "/extract?threshold=70",
            json={"query": "Men over 60 with cancer in NHS Scotland Regions"},
        )

        assert response.status_code == 200
        body = response.json()
        assert "warnings" in body
        assert any(
            "Location-based filtering is not currently supported." in warning
            for warning in body["warnings"]
        )

        assert any(
            e.get("attributes", {}).get("description", "").lower() == "cancer"
            for e in body["entities"]
        )

        assert any(
            e.get("attributes", {}).get("description", "").lower() == "male"
            for e in body["entities"]
        )

        assert has_age_constraint(body, 60, None, False, scope="query")
    finally:
        app.state.resolver_store = previous_store


def test_obese_adults_maps_to_obesity_with_age_filter():
    local_concepts = [
        {
            "concept_id": 433736,
            "concept_name": "Obesity",
            "description": "Obesity",
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
            json={"query": "Obese Adults"},
        )

        assert response.status_code == 200
        body = response.json()
        assert "entities" in body
        assert len(body["entities"]) >= 1

        assert any(
            e.get("attributes", {}).get("description", "").lower() == "obesity"
            for e in body["entities"]
        )

        assert has_age_constraint(body, 18, None, True, scope="query")
    finally:
        app.state.resolver_store = previous_store


def test_women_aged_18_45_with_endometriosis():
    local_concepts = [
        {
            "concept_id": 8532,
            "concept_name": "FEMALE",
            "description": "FEMALE",
            "domain_id": "Gender",
            "vocabulary_id": "SNOMED",
            "concept_class_id": "Gender",
            "standard_concept": "S",
        },
        {
            "concept_id": 4036219,
            "concept_name": "Endometriosis",
            "description": "Endometriosis",
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
            json={"query": "Women aged 18–45 with endometriosis"},
        )

        assert response.status_code == 200
        body = response.json()
        assert "entities" in body
        assert len(body["entities"]) >= 1

        assert any(
            e.get("attributes", {}).get("description", "").lower() == "female"
            for e in body["entities"]
        )
        assert any(
            e.get("attributes", {}).get("description", "").lower() == "endometriosis"
            for e in body["entities"]
        )

        assert has_age_constraint(body, 18, 45, True, scope="query")
    finally:
        app.state.resolver_store = previous_store


def test_men_aged_50_plus_with_bph():
    local_concepts = [
        {
            "concept_id": 8507,
            "concept_name": "MALE",
            "description": "MALE",
            "domain_id": "Gender",
            "vocabulary_id": "SNOMED",
            "concept_class_id": "Gender",
            "standard_concept": "S",
        },
        {
            "concept_id": 201819,
            "concept_name": "Benign prostatic hyperplasia",
            "description": "Benign prostatic hyperplasia",
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
            json={"query": "Men aged 50+ with benign prostatic hyperplasia"},
        )

        assert response.status_code == 200
        body = response.json()
        assert "entities" in body
        assert len(body["entities"]) >= 1

        assert any(
            e.get("attributes", {}).get("description", "").lower() == "male"
            for e in body["entities"]
        )
        assert any(
            e.get("attributes", {}).get("description", "").lower()
            == "benign prostatic hyperplasia"
            for e in body["entities"]
        )

        assert has_age_constraint(body, 50, None, True, scope="query")
    finally:
        app.state.resolver_store = previous_store


def test_negated_medication_warning_for_absence_query():
    previous_store = app.state.resolver_store
    app.state.resolver_store = LocalResolverStore(FuzzyConceptResolver([]))
    try:
        response = client.post(
            "/extract?threshold=70",
            json={
                "query": "Adults with hypertension who are not currently prescribed any antihypertensive medication"
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert "warnings" in body
        assert any(
            "Negated treatment/medication criteria may be unreliable due to incomplete records."
            in warning
            for warning in body["warnings"]
        )
    finally:
        app.state.resolver_store = previous_store


def test_occurrence_count_warning_for_multiple_courses_query():
    previous_store = app.state.resolver_store
    app.state.resolver_store = LocalResolverStore(FuzzyConceptResolver([]))
    try:
        response = client.post(
            "/extract?threshold=70",
            json={
                "query": "People with asthma who have had 2+ oral steroid courses in the last 12 months"
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert "warnings" in body
        assert any(
            "The ability to filter on multiple occurrences is not currently supported."
            in warning
            for warning in body["warnings"]
        )
    finally:
        app.state.resolver_store = previous_store


def test_age_ambiguity_warning_for_aged_query():
    local_concepts = [
        {
            "concept_id": 313217,
            "concept_name": "Atrial fibrillation",
            "description": "Atrial fibrillation",
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
            json={"query": "People aged 40+ with atrial fibrillation"},
        )

        assert response.status_code == 200
        body = response.json()
        assert "warnings" in body
        assert any(
            "Age criteria may be ambiguous (current age vs age at diagnosis)." in warning
            for warning in body["warnings"]
        )
    finally:
        app.state.resolver_store = previous_store


def test_stroke_with_last_five_years_time_constraint():
    local_concepts = [
        {
            "concept_id": 381591,
            "concept_name": "Stroke",
            "description": "Stroke",
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
            json={"query": "People with stroke recorded in the last 5 years"},
        )

        assert response.status_code == 200
        body = response.json()
        assert "entities" in body
        assert len(body["entities"]) >= 1

        assert any(
            e.get("attributes", {}).get("description", "").lower() == "stroke"
            for e in body["entities"]
        )
    finally:
        app.state.resolver_store = previous_store


def test_acronym_expansion_for_copd():
    local_concepts = [
        {
            "concept_id": 255573,
            "concept_name": "Chronic obstructive pulmonary disease",
            "description": "Chronic obstructive pulmonary disease",
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
            json={"query": "People with COPD"},
        )

        assert response.status_code == 200
        body = response.json()
        assert "entities" in body
        assert any(
            e.get("attributes", {}).get("description", "").lower()
            == "chronic obstructive pulmonary disease"
            for e in body["entities"]
        )
    finally:
        app.state.resolver_store = previous_store


def test_acronym_expansion_for_ckd():
    local_concepts = [
        {
            "concept_id": 192279,
            "concept_name": "Chronic kidney disease",
            "description": "Chronic kidney disease",
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
            json={"query": "People with CKD"},
        )

        assert response.status_code == 200
        body = response.json()
        assert "entities" in body
        assert any(
            e.get("attributes", {}).get("description", "").lower()
            == "chronic kidney disease"
            for e in body["entities"]
        )
    finally:
        app.state.resolver_store = previous_store


def test_acronym_expansion_disabled_via_env(monkeypatch):
    monkeypatch.setenv("ACRONYM_ENABLED", "false")
    importlib.reload(rules_engine)
    from parsing import QueryParser
    from rules_engine import RuleEngine

    engine = RuleEngine()
    parser = QueryParser(engine)
    resolver = FuzzyConceptResolver(
        [
            {
                "concept_id": 255573,
                "concept_name": "Chronic obstructive pulmonary disease",
                "description": "Chronic obstructive pulmonary disease",
            }
        ]
    )

    result = parser.extract("People with COPD", 70, True, resolver)
    assert not any(
        e.get("attributes", {}).get("description", "").lower()
        == "chronic obstructive pulmonary disease"
        for e in result.get("entities", [])
    )


def test_sequence_warning_for_examples():
    queries = [
        "Adults with a new diagnosis of heart failure who had no recorded hypertension beforehand",
        "People with colorectal cancer who received chemotherapy and later developed neutropenia requiring hospital admission",
        "Adults with breast cancer who later developed cardiomyopathy after anthracycline treatment",
        "People with long COVID codes and persistent breathlessness recorded 12+ weeks after acute infection",
        "Adults admitted with pneumonia who were not vaccinated before admission",
        "People with type 2 diabetes and COPD, and all-cause mortality within 5 years of COPD diagnosis",
    ]

    previous_store = app.state.resolver_store
    app.state.resolver_store = LocalResolverStore(FuzzyConceptResolver([]))
    try:
        for query in queries:
            response = client.post(
                "/extract?threshold=70",
                json={"query": query},
            )

            assert response.status_code == 200
            body = response.json()
            assert "warnings" in body
            assert any(
                "Temporal sequencing between events (A before/after B) is not supported." in warning
                for warning in body["warnings"]
            )
    finally:
        app.state.resolver_store = previous_store
