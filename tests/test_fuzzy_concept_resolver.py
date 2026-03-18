import math
import os
import pytest
from fuzzy_concept_resolver import FuzzyConceptResolver, normalise_text, tokenise


# Helpers
def make_concept(concept_id, name, ncollections=1, domain_id="Condition"):
    return {
        "concept_id": concept_id,
        "concept_name": name,
        "domain_id": domain_id,
        "ncollections": ncollections,
    }


def resolver(*concepts, threshold=70, boost_weight=1.5, **kwargs):
    env_patch = {"COLLECTION_BOOST_WEIGHT": str(boost_weight)}
    with patch_env(env_patch):
        return FuzzyConceptResolver(list(concepts), threshold=threshold, **kwargs)


class patch_env:
    """Minimal context manager to temporarily set env vars."""
    def __init__(self, mapping):
        self.mapping = mapping
        self.original = {}

    def __enter__(self):
        for k, v in self.mapping.items():
            self.original[k] = os.environ.get(k)
            os.environ[k] = v
        return self

    def __exit__(self, *_):
        for k, original_v in self.original.items():
            if original_v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = original_v

# normalise_text / tokenise

class TestNormaliseText:
    def test_lowercases(self):
        assert normalise_text("Diabetes") == "diabetes"

    def test_strips_punctuation(self):
        assert normalise_text("type-2/diabetes") == "type 2 diabetes"

    def test_collapses_whitespace(self):
        assert normalise_text("  heart   failure  ") == "heart failure"


class TestTokenise:
    def test_unigrams(self):
        unigrams, _ = tokenise("heart failure")
        assert "heart" in unigrams
        assert "failure" in unigrams

    def test_bigrams(self):
        _, phrases = tokenise("heart failure")
        assert "heart failure" in phrases

    def test_trigrams(self):
        _, phrases = tokenise("type 2 diabetes")
        assert "type 2 diabetes" in phrases

# Collection boost — isolated scoring tests

class TestCollectionBoost:
    """
    Test that ncollections > 1 raises the final score by the expected amount,
    using two near-identical concepts that differ only in ncollections.
    """

    def _make_resolver(self, boost_weight=1.5):
        concepts = [
            make_concept(1, "hypertension", ncollections=1),
            make_concept(2, "hypertension", ncollections=10),
        ]
        with patch_env({"COLLECTION_BOOST_WEIGHT": str(boost_weight)}):
            return FuzzyConceptResolver(concepts, threshold=50)

    def test_multi_collection_ranks_above_single(self):
        r = self._make_resolver()
        results = r.resolve("hypertension")
        assert len(results) == 2
        assert results[0]["concept_id"] == 2, (
            "Concept with ncollections=10 should rank first"
        )

    def test_boost_magnitude_matches_formula(self):
        """Score difference should equal log(ncollections) * weight."""
        boost_weight = 1.5
        r = self._make_resolver(boost_weight=boost_weight)
        results = r.resolve("hypertension")
        by_id = {c["concept_id"]: c["match_score"] for c in results}

        expected_boost = math.log(10) * boost_weight
        actual_diff = by_id[2] - by_id[1]
        assert abs(actual_diff - expected_boost) < 0.01, (
            f"Expected score difference {expected_boost:.3f}, got {actual_diff:.3f}"
        )

    def test_single_collection_not_boosted(self):
        """ncollections == 1 must not receive any boost."""
        boost_weight = 1.5
        r = self._make_resolver(boost_weight=boost_weight)
        results = r.resolve("hypertension")
        by_id = {c["concept_id"]: c["match_score"] for c in results}

        # With two identical strings, the base scores are the same;
        # concept 1 should have no extra bonus applied.
        r_no_boost = self._make_resolver(boost_weight=0)
        results_no_boost = r_no_boost.resolve("hypertension")
        by_id_no_boost = {c["concept_id"]: c["match_score"] for c in results_no_boost}

        assert by_id[1] == pytest.approx(by_id_no_boost[1], abs=0.01), (
            "Single-collection concept score should be unchanged by boost"
        )

    def test_zero_boost_weight_disables_boost(self):
        """Setting COLLECTION_BOOST_WEIGHT=0 must produce identical scores."""
        with patch_env({"COLLECTION_BOOST_WEIGHT": "0"}):
            r = FuzzyConceptResolver(
                [
                    make_concept(1, "hypertension", ncollections=1),
                    make_concept(2, "hypertension", ncollections=100),
                ],
                threshold=50,
            )
        results = r.resolve("hypertension")
        scores = [c["match_score"] for c in results]
        assert scores[0] == pytest.approx(scores[1], abs=0.01), (
            "With boost_weight=0, scores must be equal regardless of ncollections"
        )

    def test_boost_scales_with_ncollections(self):
        """Higher ncollections get larger boost."""
        concepts = [
            make_concept(1, "hypertension", ncollections=2),
            make_concept(2, "hypertension", ncollections=50),
            make_concept(3, "hypertension", ncollections=500),
        ]
        with patch_env({"COLLECTION_BOOST_WEIGHT": "1.5"}):
            r = FuzzyConceptResolver(concepts, threshold=50)
        results = r.resolve("hypertension")
        by_id = {c["concept_id"]: c["match_score"] for c in results}

        assert by_id[3] > by_id[2] > by_id[1], (
            "Score should increase with ncollections"
        )

    def test_missing_ncollections_treated_as_one(self):
        """Concepts without ncollections key must not crash and receive no boost."""
        concepts = [
            {"concept_id": 1, "concept_name": "hypertension"},          # no ncollections
            make_concept(2, "hypertension", ncollections=10),
        ]
        with patch_env({"COLLECTION_BOOST_WEIGHT": "1.5"}):
            r = FuzzyConceptResolver(concepts, threshold=50)
        results = r.resolve("hypertension")
        assert len(results) == 2
        by_id = {c["concept_id"]: c["match_score"] for c in results}
        assert by_id[2] > by_id[1]

# General resolver behaviour (regression guard)

class TestResolverGeneral:
    def setup_method(self):
        concepts = [
            make_concept(1, "Type 2 diabetes mellitus", ncollections=1),
            make_concept(2, "Type 1 diabetes mellitus", ncollections=1),
            make_concept(3, "Hypertension", ncollections=1),
        ]
        with patch_env({"COLLECTION_BOOST_WEIGHT": "0"}):
            self.r = FuzzyConceptResolver(concepts, threshold=70)

    def test_exact_match_returns_result(self):
        results = self.r.resolve("type 2 diabetes mellitus")
        assert any(c["concept_id"] == 1 for c in results)

    def test_no_match_returns_empty(self):
        results = self.r.resolve("zzzznotaconceptzzz")
        assert results == []

    def test_empty_input_returns_empty(self):
        assert self.r.resolve("") == []

    def test_results_sorted_by_score_descending(self):
        results = self.r.resolve("diabetes")
        scores = [c["match_score"] for c in results]
        assert scores == sorted(scores, reverse=True)

    def test_downstream_token_penalised(self):
        """A concept with a downstream token should score lower than the clean equivalent."""
        concepts = [
            make_concept(1, "diabetes", ncollections=1),
            make_concept(2, "diabetes secondary", ncollections=1),
        ]
        with patch_env({"COLLECTION_BOOST_WEIGHT": "0"}):
            r = FuzzyConceptResolver(concepts, threshold=50)
        results = r.resolve("diabetes")
        by_id = {c["concept_id"]: c["match_score"] for c in results}
        assert by_id[1] > by_id[2], (
            "Concept with 'secondary' (downstream token) should score lower"
        )
