from contextlib import contextmanager
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import app
from fuzzy_concept_resolver import FuzzyConceptResolver
from mysql_concept_resolver import MySQLConceptResolver


class LocalResolverStore:
    def __init__(self, resolver):
        self._resolver = resolver
        self.synonym_map = {}

    async def get_resolver(self):
        return self._resolver


try:
    app.state.db_config
except AttributeError:
    app.state.db_config = {}

try:
    app.state.sql_resolver
except AttributeError:
    app.state.sql_resolver = MySQLConceptResolver({})

app.state.resolver_store = LocalResolverStore(FuzzyConceptResolver([]))

client = TestClient(app)

_SEARCH_RESULT = {
    "total": 1,
    "data": [{
        "concept_id": 1,
        "name": "Diabetes",
        "category": "Condition",
        "match_score": 500,
        "ncollections": 2,
        "count": 100,
    }],
}


@contextmanager
def _sql_path(search_result=None):
    """Force the SQL resolver backend and mock MySQLConceptResolver.search."""
    result = search_result if search_result is not None else _SEARCH_RESULT
    with patch("app.RESOLVER_BACKEND", "sql"), \
         patch.object(MySQLConceptResolver, "search", return_value=result) as mock_search:
        yield mock_search


def test_extract_collection_filter_applied():
    with _sql_path() as mock_search:
        response = client.post(
            "/extract?use_collection_filter=true&collection_ids=5",
            json={"query": "diabetes"},
        )

    assert response.status_code == 200
    kwargs = mock_search.call_args[1]
    assert kwargs["use_collection_filter"] is True
    assert kwargs["collection_ids"] == [5]


def test_extract_collection_filter_not_applied_by_default():
    with _sql_path() as mock_search:
        response = client.post("/extract", json={"query": "diabetes"})

    assert response.status_code == 200
    kwargs = mock_search.call_args[1]
    assert kwargs["use_collection_filter"] is False
    assert kwargs["collection_ids"] == []


def test_extract_collection_filter_ignored_when_flag_false():
    with _sql_path() as mock_search:
        client.post(
            "/extract?use_collection_filter=false&collection_ids=9",
            json={"query": "diabetes"},
        )

    kwargs = mock_search.call_args[1]
    assert kwargs["use_collection_filter"] is False


def test_extract_multiple_collection_ids():
    with _sql_path() as mock_search:
        client.post(
            "/extract?use_collection_filter=true&collection_ids=1&collection_ids=2&collection_ids=3",
            json={"query": "diabetes"},
        )

    kwargs = mock_search.call_args[1]
    assert kwargs["collection_ids"] == [1, 2, 3]


def test_extract_stats_ordering_applied():
    with _sql_path() as mock_search:
        response = client.post(
            "/extract?use_stats_ordering=true",
            json={"query": "diabetes"},
        )

    assert response.status_code == 200
    kwargs = mock_search.call_args[1]
    assert kwargs["use_stats_ordering"] is True


def test_extract_stats_ordering_off_by_default():
    with _sql_path() as mock_search:
        client.post("/extract", json={"query": "diabetes"})

    kwargs = mock_search.call_args[1]
    assert kwargs["use_stats_ordering"] is False


def test_extract_returns_concepts_from_resolver():
    """Concepts returned by search() appear in the response entities."""
    with _sql_path():
        response = client.post("/extract", json={"query": "diabetes"})

    assert response.status_code == 200
    entities = response.json()["entities"]
    assert len(entities) == 1
    assert entities[0]["attributes"]["concept_id"] == 1
    assert entities[0]["attributes"]["concept_name"] == "Diabetes"
