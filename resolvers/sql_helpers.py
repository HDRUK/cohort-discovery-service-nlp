import json
import os
import re
from typing import Any, Dict, List, NamedTuple, Optional

from logging_config import get_logger

log = get_logger()

# Precompiled tokenisation patterns.
_NON_ALNUM = re.compile(
    r"[^a-zA-Z0-9]+"
)  # split concept names into alphanumeric tokens
_NON_ALPHA = re.compile(r"[^a-zA-Z]")  # medcat token extraction (letters only)


class SqlFragment(NamedTuple):
    """A piece of SQL text paired with the bindings it consumes.

    Keeping the two together makes placeholder/binding alignment hold by
    construction — the bindings travel with the fragment instead of being
    tracked in a separate, order-sensitive list.
    """

    sql: str
    bindings: list


class CollectionScore(NamedTuple):
    """Collection-aware scoring fragments folded into the outer query."""

    cte: SqlFragment  # "collection_stats AS (...)," — empty when no target collections
    score_expr: str
    join: str
    group_cols: List[str]  # extra GROUP BY columns the score_expr / join require

    @classmethod
    def empty(cls) -> "CollectionScore":
        """The no-collection-score case (score expression is a literal 0)."""
        return cls(SqlFragment("", []), "0", "", [])


CONCEPT_MATCH_SCORE_EXACT = int(os.getenv("CONCEPT_MATCH_SCORE_EXACT", 10000))
CONCEPT_MATCH_SCORE_CONTAINS = int(os.getenv("CONCEPT_MATCH_SCORE_CONTAINS", 500))
CONCEPT_MATCH_SCORE_PREFIX = int(os.getenv("CONCEPT_MATCH_SCORE_PREFIX", 100))
CONCEPT_MATCH_SCORE_SYNONYM = int(os.getenv("CONCEPT_MATCH_SCORE_SYNONYM", 1000))
CONCEPT_MATCH_SCORE_TOKEN = int(os.getenv("CONCEPT_MATCH_SCORE_TOKEN", 50))
_rules_path = os.getenv("RULES_PATH", "rules.json")
with open(_rules_path) as _f:
    _medcat_rules = json.load(_f).get("medcat", {})

_MEDCAT_TOKEN_STOPWORDS = set(_medcat_rules.get("token_stopwords", []))
_MEDCAT_TOKEN_MIN_LEN: int = _medcat_rules.get("token_min_len", 8)

# Collection score is quantised into small buckets. In the population branch it stays a
# sub-tier nudge (max 80 < the prefix match tier of 100) — reordering concepts within a
# match tier by popularity without ever outranking a better text match. The targeted
# branch additionally applies a strong penalty to concepts in none of the selected
# collections (see _TARGET_MISS_PENALTY).
_NCOLLECTIONS_TIERS = [(11, 40), (6, 30), (2, 20), (1, 10)]  # ">10",">5",">=2","=1"
_COUNT_TIERS = [
    (1_000_000, 70),
    (100_000, 60),
    (50_000, 50),
    (10_000, 40),
    (5_000, 33),
    (1_000, 20),
    (100, 10),
]
# In targeted mode, a concept in NONE of the selected collections is strongly demoted:
# this exceeds a full match tier, so in-collection matches are preferred even over better
# text matches that are out of collection.
_TARGET_MISS_PENALTY = 500


def _bucketed(col: str, tiers) -> str:
    """A descending CASE mapping `col` into fixed bucket points (0 below the lowest)."""
    whens = " ".join(f"WHEN {col} >= {t} THEN {p}" for t, p in tiers)
    return f"CASE {whens} ELSE 0 END"


def _placeholders(values) -> str:
    """', '-joined `%s` placeholders, one per element of a bindable sequence."""
    return ", ".join(["%s"] * len(values))


def _extract_medcat_tokens(terms: List[str]) -> List[str]:
    seen: set = set()
    tokens = []
    for term in terms:
        for word in _NON_ALPHA.sub(" ", term).lower().split():
            if (
                len(word) >= _MEDCAT_TOKEN_MIN_LEN
                and word not in _MEDCAT_TOKEN_STOPWORDS
                and word not in seen
            ):
                seen.add(word)
                tokens.append(word)
    return tokens


def find_synonym_concept_ids(
    token_index: Dict[str, List[Any]], terms: List[str]
) -> List[int]:
    """Return concept_ids whose synonyms contain all tokens from any of the given terms.

    Uses the inverted token index built at load time (see build_synonym_token_index),
    scanning only the rarest term token's candidate list rather than the whole map.
    """
    result: set = set()
    for term in terms:
        term_tokens = frozenset(term.lower().split())
        if not term_tokens:
            continue
        candidates = min((token_index.get(t, []) for t in term_tokens), key=len)
        for concept_id, syn_tokens in candidates:
            if term_tokens <= syn_tokens:
                result.add(concept_id)
    return sorted(result)


def _normalise_for_like(term: str, strip_s: bool = False) -> str:
    normalised = _NON_ALNUM.sub("%", term)
    if strip_s and normalised.endswith("s"):
        normalised = normalised[:-1]
    return normalised


def build_name_token_index(concepts: List[Dict[str, Any]]) -> Dict[str, set]:
    """Build {lowercase_token: set(concept_id)} for O(1) candidate pre-filtering.

    Called once at warmup (in enrich_resolver) and stored on the ResolverStore.
    Tokens shorter than 2 characters are excluded to avoid huge index buckets.
    """
    index: Dict[str, set] = {}
    for concept in concepts:
        name = _NON_ALNUM.sub(" ", (concept.get("concept_name") or "").lower())
        cid = concept["concept_id"]
        for token in name.split():
            if len(token) >= 2:
                index.setdefault(token, set()).add(cid)
    return index


def build_concepts_by_id(concepts: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    """Build {concept_id: {concept_id, name, category}} for child-concept hydration.

    Provides the display fields (name/category) attached to each child in the
    ancestor path, matching the ChildConcept model shape.
    """
    return {
        c["concept_id"]: {
            "concept_id": c["concept_id"],
            "name": c.get("concept_name"),
            "category": c.get("domain_id"),
        }
        for c in concepts
    }


def find_name_match_concept_ids(
    name_token_index: Dict[str, set],
    concept_names: List[str],
    medcat_names: List[str],
) -> set:
    """O(terms × index_lookup) candidate filter using the pre-built token index.

    Returns a superset of the SQL LIKE filter: all search-term tokens must be present
    in the concept name (as a set intersection, any order). MySQL rescores/filters
    exactly downstream, so over-inclusion here is safe.
    """
    result: set = set()
    for term in concept_names + medcat_names:
        term = term.strip()
        if not term:
            continue
        tokens = [t for t in _NON_ALNUM.sub(" ", term.lower()).split() if len(t) >= 2]
        if not tokens:
            continue
        # Intersect starting from the rarest (smallest) token set
        sets = sorted((name_token_index.get(t, set()) for t in tokens), key=len)
        if not sets[0]:
            continue
        candidates = sets[0].copy()
        for s in sets[1:]:
            candidates &= s
            if not candidates:
                break
        result |= candidates
    for token in _extract_medcat_tokens(medcat_names):
        result |= name_token_index.get(token, set())
    return result


def build_where_conditions(
    concept_ids: list,
    concept_names: list,
    medcat_names: list,
    synonym_concept_ids: Optional[List[int]] = None,
) -> tuple:
    conditions: List[str] = []
    bindings: List[Any] = []

    for cid in concept_ids:
        conditions.append("d.concept_id = %s")
        bindings.append(cid)

    for term in concept_names:
        term = term.strip()
        if not term:
            continue
        normalised = _normalise_for_like(term)
        conditions.append("d.concept_name LIKE %s")
        bindings.append(f"%{normalised}%")

    for term in medcat_names:
        term = term.strip()
        if not term:
            continue
        normalised = _normalise_for_like(term, strip_s=True)
        conditions.append("d.concept_name LIKE %s")
        bindings.append(f"%{normalised}%")

    for token in _extract_medcat_tokens(medcat_names):
        conditions.append("d.concept_name LIKE %s")
        bindings.append(f"%{token}%")

    if synonym_concept_ids:
        conditions.append(f"d.concept_id IN ({_placeholders(synonym_concept_ids)})")
        bindings.extend(synonym_concept_ids)

    return conditions, bindings


def build_match_score_sql(
    concept_names: list,
    medcat_names: list,
    concept_ids: list,
    synonym_concept_ids: Optional[List[int]] = None,
) -> SqlFragment:
    """Row-level text/id scoring only — no aggregates. Safe inside any GROUP BY."""
    clauses: List[str] = []
    bindings: List[Any] = []

    for term in concept_names:
        term = term.strip()
        if not term:
            continue
        term_lower = term.lower()
        clauses.append(
            f"""
            CASE
                WHEN d.concept_name = %s THEN {CONCEPT_MATCH_SCORE_EXACT}
                WHEN d.concept_name LIKE %s THEN {CONCEPT_MATCH_SCORE_CONTAINS}
                WHEN d.concept_name LIKE %s THEN {CONCEPT_MATCH_SCORE_PREFIX}
                ELSE 0
            END
            """
        )
        bindings.append(term_lower)
        bindings.append(f"%{term_lower}%")
        bindings.append(f"{term_lower}%")

    for term in medcat_names:
        term = term.strip()
        if not term:
            continue
        normalised = _normalise_for_like(term, strip_s=True).lower()
        clauses.append(
            f"""
            CASE
                WHEN d.concept_name LIKE %s THEN {CONCEPT_MATCH_SCORE_CONTAINS}
                WHEN d.concept_name LIKE %s THEN {CONCEPT_MATCH_SCORE_PREFIX}
                ELSE 0
            END
            """
        )
        bindings.append(f"%{normalised}%")
        bindings.append(f"{normalised}%")

    for token in _extract_medcat_tokens(medcat_names):
        clauses.append(
            f"CASE WHEN d.concept_name LIKE %s THEN {CONCEPT_MATCH_SCORE_TOKEN} ELSE 0 END"
        )
        bindings.append(f"%{token}%")

    for cid in concept_ids:
        clauses.append(
            f"CASE WHEN d.concept_id = %s THEN {CONCEPT_MATCH_SCORE_EXACT} ELSE 0 END"
        )
        bindings.append(cid)

    if synonym_concept_ids:
        log.debug(f"found {len(synonym_concept_ids)} synonym_concept_ids")
        clauses.append(
            f"CASE WHEN d.concept_id IN ({_placeholders(synonym_concept_ids)}) THEN {CONCEPT_MATCH_SCORE_SYNONYM} ELSE 0 END"
        )
        bindings.extend(synonym_concept_ids)

    score_sql = "(" + " + ".join(clauses) + ")" if clauses else "0"
    return SqlFragment(score_sql, bindings)


def build_collection_score_cte(
    collection_ids: Optional[List[int]] = None,
    candidate_ids: Optional[List[int]] = None,
) -> CollectionScore:
    """Build the collection-aware scoring fragments.

    With `collection_ids`: pre-aggregate membership into a `collection_stats` CTE
    (bounded to `candidate_ids` when the pre-filter fired) and score off its columns.
    Without: score off the row's own population aggregates, no CTE needed.
    """
    if not collection_ids:
        population_score = f"""
            {_bucketed("COUNT(DISTINCT d.collection_id)", _NCOLLECTIONS_TIERS)}
            + {_bucketed("SUM(d.count)", _COUNT_TIERS)}
        """
        return CollectionScore(SqlFragment("", []), population_score, "", [])

    bindings = list(collection_ids)
    candidate_clause = ""
    if candidate_ids:
        candidate_clause = f" AND concept_id IN ({_placeholders(candidate_ids)})"
        bindings += list(candidate_ids)

    cte_sql = f"""collection_stats AS (
            SELECT concept_id,
                COUNT(DISTINCT collection_id) AS target_ncollections,
                SUM(count) AS target_count
            FROM latest_distributions
            WHERE collection_id IN ({_placeholders(collection_ids)}){candidate_clause}
            GROUP BY concept_id
        ),"""
    ncollections_whens = " ".join(
        f"WHEN COALESCE(cs.target_ncollections, 0) >= {t} THEN {p}"
        for t, p in _NCOLLECTIONS_TIERS
    )
    target_score = f"CASE {ncollections_whens} ELSE -{_TARGET_MISS_PENALTY} END"
    join_sql = "LEFT JOIN collection_stats cs ON cs.concept_id = d.concept_id"
    group_cols = ["cs.target_ncollections", "cs.target_count"]
    return CollectionScore(
        SqlFragment(cte_sql, bindings), target_score, join_sql, group_cols
    )
