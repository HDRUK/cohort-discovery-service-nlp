"""Tests for the performance-oriented resolution paths:

- the inverted synonym token index (Fix 1)
- the in-memory candidate pre-filter that replaces leading-wildcard LIKE scans (Fix 2)
"""

from unittest.mock import MagicMock

from resolvers.sql_helpers import (
    build_concepts_by_id,
    build_name_token_index,
    find_name_match_concept_ids,
    find_synonym_concept_ids,
)
from loaders.synonyms import build_synonym_token_index
from resolvers import MySQLConceptResolver


# --------------------------------------------------------------------------
# Fix 1 — inverted synonym token index
# --------------------------------------------------------------------------
def _brute_force_synonyms(synonym_map, terms):
    """The original O(concepts x terms x synonyms) implementation, kept for parity checks."""
    token_sets = [set(term.lower().split()) for term in terms if term.strip()]
    return sorted(
        cid
        for cid, synonyms in synonym_map.items()
        if any(tokens <= set(syn.split()) for tokens in token_sets for syn in synonyms)
    )


def test_synonym_index_matches_brute_force():
    synonym_map = {
        1: ["myocardial infarction", "heart attack"],
        2: ["heart failure"],
        3: ["cardiac arrest", "heart attack episode"],
        4: ["asthma"],
    }
    index = build_synonym_token_index(synonym_map)

    for terms in (["heart attack"], ["heart"], ["asthma"], ["heart attack", "asthma"], ["nomatch"]):
        assert sorted(find_synonym_concept_ids(index, terms)) == _brute_force_synonyms(
            synonym_map, terms
        ), terms


def test_synonym_index_requires_all_term_tokens():
    # "heart attack" must NOT match a synonym that only has "heart"
    index = build_synonym_token_index({1: ["heart failure"], 2: ["heart attack"]})
    assert find_synonym_concept_ids(index, ["heart attack"]) == [2]


def test_synonym_index_empty_for_missing_token():
    index = build_synonym_token_index({1: ["heart attack"]})
    assert find_synonym_concept_ids(index, ["diabetes"]) == []


# --------------------------------------------------------------------------
# Fix 2 — in-memory name matching
# --------------------------------------------------------------------------
_CONCEPTS = [
    {"concept_id": 24006, "concept_name": "Sickle cell-hemoglobin C disease"},
    {"concept_id": 24007, "concept_name": "Sickle cell-thalassemia disease"},
    {"concept_id": 3027018, "concept_name": "Heart rate"},
]


def test_name_match_finds_expected_ids():
    assert find_name_match_concept_ids(build_name_token_index(_CONCEPTS), ["sickle"], []) == {24006, 24007}


def test_name_match_separator_normalisation():
    # All tokens in the search term must appear in the concept name (set intersection)
    assert find_name_match_concept_ids(build_name_token_index(_CONCEPTS), ["sickle cell-hemoglobin"], []) == {24006}


def test_name_match_no_terms_returns_empty():
    assert find_name_match_concept_ids(build_name_token_index(_CONCEPTS), [], []) == set()


# --------------------------------------------------------------------------
# Fix 2 — resolver fast path (concept list populated -> concept_id IN)
# --------------------------------------------------------------------------
class _Resolver:
    def __init__(self, concepts):
        self.concepts = concepts


class _Store:
    def __init__(self, concepts, ancestor_map=None):
        self.resolver = _Resolver(concepts)
        self.synonym_map = {}
        self.synonym_token_index = {}
        self.acronym_index = {}
        self.name_token_index = build_name_token_index(concepts)
        self.ancestor_map = ancestor_map or {}
        self.concepts_by_id = build_concepts_by_id(concepts)


def _mock_engine(rows):
    cursor = MagicMock()
    cursor.fetchall.return_value = rows
    raw_conn = MagicMock()
    raw_conn.cursor.return_value = cursor
    engine = MagicMock()
    engine.raw_connection.return_value = raw_conn
    return engine, cursor


def test_fast_path_queries_by_concept_id():
    rows = [{"concept_id": 24006, "name": "Sickle cell-hemoglobin C disease",
             "category": "Condition", "match_score": 500, "collection_score": 0,
             "ncollections": 1, "count": 10, "cnt": 2}]
    engine, cursor = _mock_engine(rows)
    resolver = MySQLConceptResolver(engine, _Store(_CONCEPTS))

    resolver.search(concept_names=["sickle"], include_ancestors=False)

    sql, bindings = cursor.execute.call_args[0]
    assert "d.concept_id IN" in sql
    assert "d.concept_name LIKE" not in sql.split("WHERE", 1)[1].split("GROUP BY")[0]
    assert 24006 in bindings and 24007 in bindings
    assert 3027018 not in bindings


def test_fast_path_no_match_skips_query():
    engine, cursor = _mock_engine([])
    resolver = MySQLConceptResolver(engine, _Store(_CONCEPTS))

    result = resolver.search(concept_names=["no_such_concept"], include_ancestors=False)

    assert result == {"total": 0, "data": []}
    cursor.execute.assert_not_called()


def test_include_ancestors_hydrates_children_from_map():
    concepts = [
        {"concept_id": 24006, "concept_name": "Sickle cell-hemoglobin C disease", "domain_id": "Condition"},
        {"concept_id": 24007, "concept_name": "Sickle cell-thalassemia disease", "domain_id": "Condition"},
    ]
    row = {"concept_id": 24006, "name": "Sickle cell-hemoglobin C disease",
           "category": "Condition", "match_score": 500, "collection_score": 0,
           "ncollections": 1, "count": 10, "cnt": 1}
    cnt_cursor = MagicMock()
    cnt_cursor.fetchone.return_value = {"cnt": 1}
    cnt_cursor.fetchall.return_value = [row]
    raw_conn = MagicMock()
    raw_conn.cursor.return_value = cnt_cursor
    engine = MagicMock()
    engine.raw_connection.return_value = raw_conn

    resolver = MySQLConceptResolver(engine, _Store(concepts, ancestor_map={24006: [24007]}))
    result = resolver.search(concept_names=["sickle"], include_ancestors=True)

    children = result["data"][0]["children"]
    assert children == [{"concept_id": 24007, "name": "Sickle cell-thalassemia disease", "category": "Condition"}]


# --------------------------------------------------------------------------
# reduced mode during warm-up (store.resolver is None) -> skip enrichment
# --------------------------------------------------------------------------
class _ColdStore:
    """Store mid warm-up: resolver not yet set, indexes still empty."""

    def __init__(self):
        self.resolver = None
        self.synonym_map = {}
        self.synonym_token_index = {}
        self.acronym_index = {}
        self.name_token_index = {}
        self.ancestor_map = {}
        self.concepts_by_id = {}


def test_reduced_mode_uses_like_and_skips_extras():
    rows = [{"concept_id": 24006, "name": "Sickle cell-hemoglobin C disease",
             "category": "Condition", "match_score": 500, "collection_score": 0,
             "ncollections": 1, "count": 10, "cnt": 1}]
    engine, cursor = _mock_engine(rows)
    cursor.fetchone.return_value = {"cnt": 1}
    medcat = MagicMock()
    resolver = MySQLConceptResolver(engine, _ColdStore(), medcat_client=medcat)

    # Ask for everything; warm-up should force it all off.
    resolver.search(
        concept_names=["sickle"],
        collection_ids=[1, 3],
        include_ancestors=True,
        use_medcat=True,
        use_synonym_lookup=True,
        use_collection_score=True,
    )

    # MedCAT is not called while warming up.
    medcat.expand.assert_not_called()

    sql, bindings = cursor.execute.call_args[0]  # last call == main query
    assert "d.concept_name LIKE" in sql          # LIKE fallback, no name-token index
    assert "collection_stats AS" not in sql      # collection scoring skipped
    assert "d.concept_id IN" not in sql          # no fast candidate pre-filter
    assert sql.count("%s") == len(bindings)       # placeholder/binding alignment guard


# --------------------------------------------------------------------------
# collection_stats CTE bounded to candidate ids (cold-scan optimisation)
# --------------------------------------------------------------------------
def test_collection_cte_bounded_to_candidate_ids():
    from resolvers.sql_helpers import build_collection_score_cte

    # With candidates: CTE is restricted to them; bindings = collections + candidates, aligned.
    cte, _, _, _ = build_collection_score_cte([1, 3], [24006, 24007])
    assert "AND concept_id IN (%s, %s)" in cte.sql
    assert cte.sql.count("%s") == len(cte.bindings)
    assert cte.bindings == [1, 3, 24006, 24007]

    # Without candidates: unbounded (previous behaviour), backward compatible.
    cte2, _, _, _ = build_collection_score_cte([1, 3])
    assert "concept_id IN" not in cte2.sql
    assert cte2.bindings == [1, 3]


def test_fast_path_bounds_collection_cte_and_aligns_bindings():
    rows = [{"concept_id": 24006, "name": "Sickle cell-hemoglobin C disease",
             "category": "Condition", "match_score": 500, "collection_score": 0,
             "ncollections": 1, "count": 10, "cnt": 1}]
    engine, cursor = _mock_engine(rows)
    resolver = MySQLConceptResolver(engine, _Store(_CONCEPTS))

    resolver.search(concept_names=["sickle"], collection_ids=[1, 3], include_ancestors=False)

    sql, bindings = cursor.execute.call_args[0]  # last call == main query
    cte = sql.split("base AS", 1)[0]
    assert "collection_stats AS" in cte
    assert "AND concept_id IN" in cte  # CTE bounded, not scanning the whole collection
    assert sql.count("%s") == len(bindings)  # placeholder/binding alignment guard
    assert 24006 in bindings and 24007 in bindings


# --------------------------------------------------------------------------
# collection_score is quantised into bounded buckets (never dominates match_score)
# --------------------------------------------------------------------------
def test_collection_score_is_quantised_and_bounded():
    from resolvers.sql_helpers import build_collection_score_cte

    # Population branch (no collection_ids): bucketed CASE on ncollections + count, no SQRT.
    population = build_collection_score_cte().score_expr
    assert "WHEN COUNT(DISTINCT d.collection_id) >= 11 THEN 40" in population
    assert "WHEN SUM(d.count) >= 1000000 THEN 70" in population
    assert "SQRT" not in population

    # Targeted branch (collection_ids given): bucketed on target collections, no SQRT,
    # and a strong negative penalty for concepts in none of the selected collections.
    targeted = build_collection_score_cte([1, 3]).score_expr
    assert "WHEN COALESCE(cs.target_ncollections, 0) >= 11 THEN 40" in targeted
    assert "ELSE -500 END" in targeted
    assert "SQRT" not in targeted
