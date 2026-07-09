from fastapi.testclient import TestClient

from app import app
from resolvers import FuzzyConceptResolver


class LocalResolverStore:
    def __init__(self, resolver):
        self._resolver = resolver
        self.has_loaded_core = True
        self.has_loaded_acronyms = True
        self.has_loaded_synonyms = True
        self.has_loaded_ancestors = True
        self.fully_warm = True

    @property
    def resolver(self):
        return self._resolver

    async def get_resolver(self):
        return self._resolver


client = TestClient(app)
try:
    app.state.resolver_store
except AttributeError:
    app.state.resolver_store = LocalResolverStore(FuzzyConceptResolver([]))


PAREN_CONCEPTS = [
    {
        "concept_id": 8507,
        "concept_name": "Male",
        "description": "Male",
        "domain_id": "Gender",
        "vocabulary_id": "Gender",
        "concept_class_id": "Gender",
        "standard_concept": "S",
        "concept_code": "M",
    },
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
        assert group["text"] == "COPD and asthma"
        assert group["operator"] == "and"
        assert not body["warnings"]
        assert body["entities"] == []

        group_descriptions = {
            e["attributes"].get("description", "").lower() for e in group["entities"]
        }

        assert "chronic obstructive pulmonary disease" in group_descriptions
        assert "asthma" in group_descriptions
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

        group_descriptions = {
            e["attributes"].get("description", "").lower() for e in group["entities"]
        }

        assert "chronic obstructive pulmonary disease" in group_descriptions
        assert "asthma" in group_descriptions
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

        group = body["groups"][0]
        assert group["text"] == "COPD or asthma"
        assert group["operator"] == "or"

        group_descriptions = {
            e["attributes"].get("description", "").lower() for e in group["entities"]
        }

        assert "chronic obstructive pulmonary disease" in group_descriptions
        assert "asthma" in group_descriptions
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


def test_men_with_condition_group_combined_with_outer_or_entity():
    """A parenthesised demographic + condition group can be combined with an outer OR entity."""
    previous_store = app.state.resolver_store
    app.state.resolver_store = LocalResolverStore(FuzzyConceptResolver(PAREN_CONCEPTS))
    try:
        response = client.post(
            "/extract?threshold=70",
            json={"query": "(men with COPD) or asthma"},
        )
        assert response.status_code == 200
        body = response.json()

        assert not body["warnings"]

        assert len(body["groups"]) == 1
        group = body["groups"][0]

        assert group["text"] == "men with COPD"

        assert group["operator"] is None

        group_descriptions = {
            e["attributes"].get("description", "").lower() for e in group["entities"]
        }

        assert "male" in group_descriptions
        assert "chronic obstructive pulmonary disease" in group_descriptions
        assert "asthma" not in group_descriptions

        outer_descriptions = {
            e["attributes"].get("description", "").lower() for e in body["entities"]
        }

        assert "asthma" in outer_descriptions
        assert "male" not in outer_descriptions
        assert "chronic obstructive pulmonary disease" not in outer_descriptions

        outer_texts = {e["text"].lower() for e in body["entities"]}
        assert "asthma" in outer_texts
        assert "or asthma" not in outer_texts
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

        group = body["groups"][0]
        assert group["text"] == "COPD"
        assert group["operator"] is None
        assert not body["warnings"]

        group_descriptions = {
            e["attributes"].get("description", "").lower() for e in group["entities"]
        }

        assert "chronic obstructive pulmonary disease" in group_descriptions
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
        assert group["text"] == "COPD over the age of 50"
        assert group["operator"] is None

        assert any(c.get("min") == 50 for c in group["age_constraints"])

        group_descriptions = {
            e["attributes"].get("description", "").lower() for e in group["entities"]
        }

        assert "chronic obstructive pulmonary disease" in group_descriptions
    finally:
        app.state.resolver_store = previous_store


# ---------------------------------------------------------------------------
# Top-level OR — root_operator and root_groups
# ---------------------------------------------------------------------------


def test_top_level_or_returns_root_operator():
    previous_store = app.state.resolver_store
    app.state.resolver_store = LocalResolverStore(FuzzyConceptResolver(PAREN_CONCEPTS))
    try:
        response = client.post(
            "/extract?threshold=70",
            json={"query": "COPD or asthma"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["root_operator"] == "or"
    finally:
        app.state.resolver_store = previous_store


def test_top_level_or_root_groups_have_correct_entities():
    previous_store = app.state.resolver_store
    app.state.resolver_store = LocalResolverStore(FuzzyConceptResolver(PAREN_CONCEPTS))
    try:
        response = client.post(
            "/extract?threshold=70",
            json={"query": "COPD or asthma"},
        )
        assert response.status_code == 200
        body = response.json()
        assert len(body["root_groups"]) == 2

        group_0_descriptions = {
            e["attributes"].get("description", "").lower()
            for e in body["root_groups"][0]["entities"]
        }
        group_1_descriptions = {
            e["attributes"].get("description", "").lower()
            for e in body["root_groups"][1]["entities"]
        }

        assert "chronic obstructive pulmonary disease" in group_0_descriptions
        assert "asthma" in group_1_descriptions
    finally:
        app.state.resolver_store = previous_store


def test_top_level_or_backward_compat_entities_flattened():
    """root_operator queries still populate the top-level entities array."""
    previous_store = app.state.resolver_store
    app.state.resolver_store = LocalResolverStore(FuzzyConceptResolver(PAREN_CONCEPTS))
    try:
        response = client.post(
            "/extract?threshold=70",
            json={"query": "COPD or asthma"},
        )
        assert response.status_code == 200
        body = response.json()
        descriptions = {
            e["attributes"].get("description", "").lower() for e in body["entities"]
        }
        assert "chronic obstructive pulmonary disease" in descriptions
        assert "asthma" in descriptions
    finally:
        app.state.resolver_store = previous_store


def test_triple_top_level_or_produces_three_root_groups():
    previous_store = app.state.resolver_store
    app.state.resolver_store = LocalResolverStore(FuzzyConceptResolver(PAREN_CONCEPTS))
    try:
        response = client.post(
            "/extract?threshold=70",
            json={"query": "COPD or asthma or type 2 diabetes mellitus"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["root_operator"] == "or"
        assert len(body["root_groups"]) == 3
    finally:
        app.state.resolver_store = previous_store


def test_paren_group_or_bare_entity_returns_root_operator():
    """(COPD or asthma) or diabetes — top-level OR with a paren group on one side."""
    previous_store = app.state.resolver_store
    app.state.resolver_store = LocalResolverStore(FuzzyConceptResolver(PAREN_CONCEPTS))
    try:
        response = client.post(
            "/extract?threshold=70",
            json={"query": "(COPD or asthma) or type 2 diabetes mellitus"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["root_operator"] == "or"
        assert len(body["root_groups"]) == 2

        # First root group has a paren sub-group, no flat entities
        first_root_group_entities = body["root_groups"][0]["entities"]
        first_root_group_groups = body["root_groups"][0]["groups"]
        assert first_root_group_entities == []
        assert len(first_root_group_groups) == 1
        assert first_root_group_groups[0]["operator"] == "or"

        # Second root group has diabetes as a flat entity
        second_descriptions = {
            e["attributes"].get("description", "").lower()
            for e in body["root_groups"][1]["entities"]
        }
        assert "type 2 diabetes mellitus" in second_descriptions
    finally:
        app.state.resolver_store = previous_store


def test_and_query_has_no_root_operator():
    previous_store = app.state.resolver_store
    app.state.resolver_store = LocalResolverStore(FuzzyConceptResolver(PAREN_CONCEPTS))
    try:
        response = client.post(
            "/extract?threshold=70",
            json={"query": "COPD and asthma"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body.get("root_operator") is None
        assert body.get("root_groups", []) == []
    finally:
        app.state.resolver_store = previous_store


def test_leading_demographic_constraint_propagates_to_all_or_groups():
    """'adults with asthma or type 2 diabetes mellitus' — adults age constraint
    must apply to both root groups, not only the first."""
    previous_store = app.state.resolver_store
    app.state.resolver_store = LocalResolverStore(FuzzyConceptResolver(PAREN_CONCEPTS))
    try:
        response = client.post(
            "/extract?threshold=70",
            json={"query": "adults with asthma or type 2 diabetes mellitus"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["root_operator"] == "or"
        assert len(body["root_groups"]) == 2

        for rg in body["root_groups"]:
            age_constraints = rg.get("age_constraints", [])
            assert any(
                c.get("min") == 18 and c.get("max") is None and c.get("inclusive") is True
                for c in age_constraints
            ), f"Expected adults (min=18) constraint in root_group, got: {age_constraints}"
    finally:
        app.state.resolver_store = previous_store


def test_per_group_demographic_constraints_not_cross_applied():
    """'adults with asthma or children with type 2 diabetes mellitus' — each
    group keeps its own distinct age constraint; neither is propagated."""
    previous_store = app.state.resolver_store
    app.state.resolver_store = LocalResolverStore(FuzzyConceptResolver(PAREN_CONCEPTS))
    try:
        response = client.post(
            "/extract?threshold=70",
            json={"query": "adults with asthma or children with type 2 diabetes mellitus"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["root_operator"] == "or"
        assert len(body["root_groups"]) == 2

        group_0_age = body["root_groups"][0].get("age_constraints", [])
        group_1_age = body["root_groups"][1].get("age_constraints", [])

        # Group 0 has adults (min=18), group 1 has children (max=17)
        assert any(c.get("min") == 18 for c in group_0_age), f"group 0: {group_0_age}"
        assert any(c.get("max") == 17 for c in group_1_age), f"group 1: {group_1_age}"
        # Neither group should have both constraints
        assert not any(c.get("max") == 17 for c in group_0_age)
        assert not any(c.get("min") == 18 for c in group_1_age)
    finally:
        app.state.resolver_store = previous_store


# ---------------------------------------------------------------------------
# Negative paths — warnings are returned, but payloads are still present
# ---------------------------------------------------------------------------


def test_or_group_combined_with_outer_and_entity():
    previous_store = app.state.resolver_store
    app.state.resolver_store = LocalResolverStore(FuzzyConceptResolver(PAREN_CONCEPTS))
    try:
        response = client.post(
            "/extract?threshold=70",
            json={"query": "(COPD or asthma) and type 2 diabetes mellitus"},
        )
        assert response.status_code == 200
        body = response.json()

        assert not body["warnings"]

        assert len(body["groups"]) == 1
        group = body["groups"][0]

        assert group["text"] == "COPD or asthma"
        assert group["operator"] == "or"

        group_descriptions = {
            e["attributes"].get("description", "").lower() for e in group["entities"]
        }

        assert "chronic obstructive pulmonary disease" in group_descriptions
        assert "asthma" in group_descriptions
        assert "type 2 diabetes mellitus" not in group_descriptions

        outer_descriptions = {
            e["attributes"].get("description", "").lower() for e in body["entities"]
        }

        assert "type 2 diabetes mellitus" in outer_descriptions
        assert "chronic obstructive pulmonary disease" not in outer_descriptions
        assert "asthma" not in outer_descriptions

        outer_texts = {e["text"].lower() for e in body["entities"]}
        assert "type 2 diabetes mellitus" in outer_texts
        assert "and type 2 diabetes mellitus" not in outer_texts
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
            "Missing opening or closing parenthesis" in w for w in body["warnings"]
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
            "Missing opening or closing parenthesis" in w for w in body["warnings"]
        )
        assert body["groups"] == []
        assert any(
            e["attributes"].get("description", "").lower()
            == "chronic obstructive pulmonary disease"
            for e in body["entities"]
        )
    finally:
        app.state.resolver_store = previous_store
