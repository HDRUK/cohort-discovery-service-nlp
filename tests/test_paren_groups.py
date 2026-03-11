from fastapi.testclient import TestClient

from app import app
from fuzzy_concept_resolver import FuzzyConceptResolver


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


PAREN_CONCEPTS = [
    {
        "concept_id": 255573,
        "concept_name": "Chronic obstructive pulmonary disease",
        "description": "Chronic obstructive pulmonary disease",
        "domain_id": "Condition",
        "vocabulary_id": "SNOMED",
        "concept_class_id": "Disorder",
        "standard_concept": "S",
    },
    {
        "concept_id": 317009,
        "concept_name": "Asthma",
        "description": "Asthma",
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


# ---------------------------------------------------------------------------
# Positive paths
# ---------------------------------------------------------------------------


def test_query_without_parentheses_has_empty_groups():
    previous_store = app.state.resolver_store
    app.state.resolver_store = LocalResolverStore(FuzzyConceptResolver(PAREN_CONCEPTS))
    try:
        response = client.post(
            "/extract?threshold=70",
            json={"query": "COPD and asthma"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body.get("groups", []) == []
        assert not body["warnings"]
    finally:
        app.state.resolver_store = previous_store


def test_valid_and_group_returns_group_with_and_operator():
    previous_store = app.state.resolver_store
    app.state.resolver_store = LocalResolverStore(FuzzyConceptResolver(PAREN_CONCEPTS))
    try:
        response = client.post(
            "/extract?threshold=70",
            json={"query": "(COPD and asthma)"},
        )
        assert response.status_code == 200
        body = response.json()
        assert "groups" in body
        assert len(body["groups"]) == 1
        group = body["groups"][0]
        assert group["operator"] == "and"
        assert len(group["entities"]) >= 1
        assert not body["warnings"]
    finally:
        app.state.resolver_store = previous_store


def test_valid_or_group_returns_group_with_or_operator():
    previous_store = app.state.resolver_store
    app.state.resolver_store = LocalResolverStore(FuzzyConceptResolver(PAREN_CONCEPTS))
    try:
        response = client.post(
            "/extract?threshold=70",
            json={"query": "(COPD or asthma)"},
        )
        assert response.status_code == 200
        body = response.json()
        assert "groups" in body
        assert len(body["groups"]) == 1
        group = body["groups"][0]
        assert group["operator"] == "or"
        assert not body["warnings"]
    finally:
        app.state.resolver_store = previous_store


def test_group_text_matches_parenthesised_content():
    previous_store = app.state.resolver_store
    app.state.resolver_store = LocalResolverStore(FuzzyConceptResolver(PAREN_CONCEPTS))
    try:
        response = client.post(
            "/extract?threshold=70",
            json={"query": "People with (COPD or asthma)"},
        )
        assert response.status_code == 200
        body = response.json()
        assert len(body["groups"]) == 1
        assert body["groups"][0]["text"] == "COPD or asthma"
    finally:
        app.state.resolver_store = previous_store


def test_group_entities_are_separate_from_outer_entities():
    """Concepts inside parens appear in the group, not the outer entities array."""
    previous_store = app.state.resolver_store
    app.state.resolver_store = LocalResolverStore(FuzzyConceptResolver(PAREN_CONCEPTS))
    try:
        response = client.post(
            "/extract?threshold=70",
            json={"query": "Type 2 diabetes mellitus and (COPD and asthma)"},
        )
        assert response.status_code == 200
        body = response.json()

        outer_descriptions = {
            e["attributes"].get("description", "").lower() for e in body["entities"]
        }
        assert "type 2 diabetes mellitus" in outer_descriptions
        assert "chronic obstructive pulmonary disease" not in outer_descriptions
        assert "asthma" not in outer_descriptions

        assert len(body["groups"]) == 1
        group_descriptions = {
            e["attributes"].get("description", "").lower()
            for e in body["groups"][0]["entities"]
        }
        assert "chronic obstructive pulmonary disease" in group_descriptions
        assert "asthma" in group_descriptions
    finally:
        app.state.resolver_store = previous_store


def test_group_with_single_concept_has_no_operator():
    """A group containing a single concept with no logical connectors returns operator=None."""
    previous_store = app.state.resolver_store
    app.state.resolver_store = LocalResolverStore(FuzzyConceptResolver(PAREN_CONCEPTS))
    try:
        response = client.post(
            "/extract?threshold=70",
            json={"query": "Adults with (COPD)"},
        )
        assert response.status_code == 200
        body = response.json()
        assert len(body["groups"]) == 1
        assert body["groups"][0]["operator"] is None
        assert len(body["groups"][0]["entities"]) >= 1
        assert not body["warnings"]
    finally:
        app.state.resolver_store = previous_store


def test_group_age_constraints_captured_within_group():
    """Age constraints expressed inside parens are returned on the group, not the outer query."""
    previous_store = app.state.resolver_store
    app.state.resolver_store = LocalResolverStore(FuzzyConceptResolver(PAREN_CONCEPTS))
    try:
        response = client.post(
            "/extract?threshold=70",
            json={"query": "Adults with asthma (COPD over the age of 50)"},
        )
        assert response.status_code == 200
        body = response.json()
        assert len(body["groups"]) == 1
        group = body["groups"][0]
        assert any(
            c.get("min") == 50 for c in group["age_constraints"]
        )
    finally:
        app.state.resolver_store = previous_store


# ---------------------------------------------------------------------------
# Negative paths — warnings are returned, but payloads are still present
# ---------------------------------------------------------------------------


def test_mixed_operators_in_group_returns_warning_and_group():
    """Mixed and/or operators in a group trigger a warning; the group is still returned."""
    previous_store = app.state.resolver_store
    app.state.resolver_store = LocalResolverStore(FuzzyConceptResolver(PAREN_CONCEPTS))
    try:
        response = client.post(
            "/extract?threshold=70",
            json={"query": "Adults (COPD or asthma and type 2 diabetes mellitus)"},
        )
        assert response.status_code == 200
        body = response.json()

        assert any(
            "All operators within a group must be the same" in w
            for w in body["warnings"]
        )
        # Group still returned with entities despite invalid operator mix
        assert len(body["groups"]) == 1
        assert body["groups"][0]["operator"] is None
        assert len(body["groups"][0]["entities"]) >= 1
    finally:
        app.state.resolver_store = previous_store


def test_missing_closing_parenthesis_returns_warning_with_entities():
    """Unmatched opening paren emits a warning; outer entities are still resolved."""
    previous_store = app.state.resolver_store
    app.state.resolver_store = LocalResolverStore(FuzzyConceptResolver(PAREN_CONCEPTS))
    try:
        response = client.post(
            "/extract?threshold=70",
            json={"query": "People with COPD (and asthma"},
        )
        assert response.status_code == 200
        body = response.json()

        assert any(
            "Missing opening or closing parenthesis" in w
            for w in body["warnings"]
        )
        # No groups when parens are invalid
        assert body["groups"] == []
        # Entities still resolved from the full query text
        assert any(
            e["attributes"].get("description", "").lower()
            == "chronic obstructive pulmonary disease"
            for e in body["entities"]
        )
    finally:
        app.state.resolver_store = previous_store


def test_missing_opening_parenthesis_returns_warning_with_entities():
    """Unmatched closing paren emits a warning; outer entities are still resolved."""
    previous_store = app.state.resolver_store
    app.state.resolver_store = LocalResolverStore(FuzzyConceptResolver(PAREN_CONCEPTS))
    try:
        response = client.post(
            "/extract?threshold=70",
            json={"query": "People with COPD and asthma)"},
        )
        assert response.status_code == 200
        body = response.json()

        assert any(
            "Missing opening or closing parenthesis" in w
            for w in body["warnings"]
        )
        assert body["groups"] == []
        assert any(
            e["attributes"].get("description", "").lower()
            == "chronic obstructive pulmonary disease"
            for e in body["entities"]
        )
    finally:
        app.state.resolver_store = previous_store
