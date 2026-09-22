from contextlib import contextmanager
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import app
from resolvers import MySQLConceptResolver

client = TestClient(app)

PLAN = {
    "age": [18, 120],
    "sex": [],
    "death": "any",
    "op": "or",
    "rules": [{"term": "cancer"}, {"term": "diabetes"}],
}

SEARCH_RESULT = {
    "total": 2,
    "data": [
        {
            "concept_id": 443392,
            "name": "Malignant neoplastic disease",
            "category": "Condition",
            "match_score": 500,
            "collection_score": 0,
            "ncollections": 2,
            "count": 100,
        },
        {
            "concept_id": 4112853,
            "name": "Secondary malignant neoplastic disease",
            "category": "Condition",
            "match_score": 300,
            "collection_score": 0,
            "ncollections": 1,
            "count": 50,
        },
    ],
}


class StubOllamaClient:
    model = "qwen3:8b"

    def __init__(self, plan=None, error=None):
        self._plan = plan if plan is not None else PLAN
        self._error = error
        self.calls = []

    def plan(self, query, model=None):
        self.calls.append((query, model))
        if self._error:
            raise self._error
        return self._plan


@contextmanager
def _client(stub=None, search_result=None):
    previous = getattr(app.state, "ollama_client", None)
    app.state.ollama_client = StubOllamaClient() if stub is None else stub
    result = SEARCH_RESULT if search_result is None else search_result
    try:
        with patch.object(
            MySQLConceptResolver, "search", return_value=result
        ) as mock_search:
            yield mock_search
    finally:
        app.state.ollama_client = previous


def _leaves(tree):
    return [node for node in tree["rules"] if "rule" in node]


def test_returns_503_when_ollama_is_not_configured():
    previous = getattr(app.state, "ollama_client", None)
    app.state.ollama_client = None
    try:
        response = client.post("/llm/parse", json={"query": "adults with cancer"})
    finally:
        app.state.ollama_client = previous

    assert response.status_code == 503
    assert "OLLAMA_URL" in response.json()["detail"]


def test_returns_502_when_ollama_errors():
    stub = StubOllamaClient(error=ValueError("boom"))
    with _client(stub):
        response = client.post("/llm/parse", json={"query": "adults with cancer"})

    assert response.status_code == 502
    assert "boom" in response.json()["detail"]


def test_tree_has_interleaved_or_and_demographics():
    with _client():
        response = client.post(
            "/llm/parse", json={"query": "adults with cancer or diabetes"}
        )

    assert response.status_code == 200
    tree = response.json()["tree"]
    assert tree["rules"][1]["combinator"] == "or"
    assert tree["demographics"]["age"] == [18, 120]


def test_concepts_are_filled_from_the_resolver():
    with _client() as mock_search:
        response = client.post(
            "/llm/parse", json={"query": "adults with cancer or diabetes"}
        )

    assert mock_search.call_count == 2
    concept = _leaves(response.json()["tree"])[0]["rule"]["concept"]
    assert concept["concept_id"] == 443392
    assert concept["name"] == "Malignant neoplastic disease"
    assert concept["category"] == "Condition"


def test_remaining_matches_become_alternatives():
    with _client():
        response = client.post(
            "/llm/parse", json={"query": "adults with cancer or diabetes"}
        )

    concept = _leaves(response.json()["tree"])[0]["rule"]["concept"]
    assert [alt["concept_id"] for alt in concept["alternatives"]] == [4112853]


def test_search_term_is_passed_to_the_resolver():
    with _client() as mock_search:
        client.post("/llm/parse", json={"query": "adults with cancer or diabetes"})

    terms = [call[1]["concept_names"] for call in mock_search.call_args_list]
    assert terms == [["cancer"], ["diabetes"]]


def test_fill_concepts_false_leaves_every_concept_blank():
    with _client() as mock_search:
        response = client.post(
            "/llm/parse",
            json={"query": "adults with cancer or diabetes", "fill_concepts": False},
        )

    assert mock_search.call_count == 0
    leaves = _leaves(response.json()["tree"])
    assert [leaf["rule"]["concept"] for leaf in leaves] == [None, None]
    assert [leaf["searchTerm"] for leaf in leaves] == ["cancer", "diabetes"]


def test_unmatched_term_gets_the_placeholder_concept_and_a_warning():
    with _client(search_result={"total": 0, "data": []}):
        response = client.post(
            "/llm/parse", json={"query": "adults with cancer or diabetes"}
        )

    body = response.json()
    concept = _leaves(body["tree"])[0]["rule"]["concept"]
    assert concept["concept_id"] is None
    assert concept["name"] == "cancer"
    assert (
        'We cannot find any matching concepts for your term "cancer".' in body["warnings"]
    )
    assert body["tree"]["warnings"] == body["warnings"]


def test_model_override_is_forwarded_and_reported():
    stub = StubOllamaClient()
    with _client(stub):
        response = client.post(
            "/llm/parse",
            json={"query": "adults with cancer", "model": "qwen3:14b"},
        )

    assert stub.calls == [("adults with cancer", "qwen3:14b")]
    assert response.json()["model"] == "qwen3:14b"


def test_plan_and_timings_are_returned():
    with _client():
        response = client.post(
            "/llm/parse", json={"query": "adults with cancer or diabetes"}
        )

    body = response.json()
    assert body["plan"] == PLAN
    assert set(body["duration_ms"]) == {"llm", "resolve"}


def test_default_max_matches_is_forwarded_to_the_resolver():
    with _client() as mock_search:
        client.post("/llm/parse", json={"query": "adults with cancer"})

    assert mock_search.call_args[1]["per_page"] == 10


def test_max_matches_query_param_overrides_the_default():
    with _client() as mock_search:
        client.post("/llm/parse?max_matches=3", json={"query": "adults with cancer"})

    assert mock_search.call_args[1]["per_page"] == 3


def test_fill_concepts_query_param_skips_resolution():
    with _client() as mock_search:
        response = client.post(
            "/llm/parse?fill_concepts=false", json={"query": "adults with cancer"}
        )

    assert mock_search.call_count == 0
    assert _leaves(response.json()["tree"])[0]["rule"]["concept"] is None


def test_fill_concepts_query_param_overrides_the_body_field():
    with _client() as mock_search:
        client.post(
            "/llm/parse?fill_concepts=true",
            json={"query": "adults with cancer", "fill_concepts": False},
        )

    assert mock_search.call_count == 2


def test_body_field_still_applies_when_the_query_param_is_absent():
    with _client() as mock_search:
        client.post(
            "/llm/parse", json={"query": "adults with cancer", "fill_concepts": False}
        )

    assert mock_search.call_count == 0
