import re
from typing import Any, Dict, List, Optional, Tuple

from rules_engine import RuleEngine


class QueryParser:
    def __init__(self, engine: RuleEngine):
        self.engine = engine
        self._acronym_index = {}
        self._acronym_cache_id = None

    def _build_acronym_index(self, concepts: List[Dict[str, Any]]) -> Dict[str, List[str]]:
        return self.engine.build_acronym_index(concepts)

    def _get_acronym_index(self, resolver: Any) -> Dict[str, List[str]]:
        eager_index = getattr(resolver, "acronym_index", None)
        if eager_index is not None:
            return eager_index
        concepts = getattr(resolver, "concepts", None)
        if concepts is None:
            return {}
        cache_id = id(concepts)
        if cache_id != self._acronym_cache_id:
            self._acronym_index = self._build_acronym_index(concepts)
            self._acronym_cache_id = cache_id
        return self._acronym_index

    def _validate_paren_groups(self, query: str) -> Tuple[List[Tuple[int, int, str]], List[str]]:
        """Returns (groups, warnings). groups is a list of (open_idx, close_idx, inner_text)."""
        stack: List[int] = []
        groups: List[Tuple[int, int, str]] = []

        for i, ch in enumerate(query):
            if ch == "(":
                stack.append(i)
            elif ch == ")":
                if not stack:
                    return [], ["Missing opening or closing parenthesis"]
                open_idx = stack.pop()
                groups.append((open_idx, i, query[open_idx + 1 : i]))

        if stack:
            return [], ["Missing opening or closing parenthesis"]

        return groups, []

    def _detect_group_operator(self, text: str) -> Optional[str]:
        """Returns 'and', 'or', or None if both (mixed) or neither are present."""
        has_and = bool(re.search(r"\band\b", text, re.IGNORECASE))
        has_or = bool(re.search(r"\bor\b", text, re.IGNORECASE))
        if has_and and has_or:
            return None
        if has_and:
            return "and"
        if has_or:
            return "or"
        return None

    def _expand_acronyms(self, text: str, resolver: Any) -> str:
        rules = self.engine.acronym_rules
        if not rules.get("enabled", True):
            return text
        min_len = int(rules.get("min_len", 2))
        max_len = int(rules.get("max_len", 6))
        index = self._get_acronym_index(resolver)
        if not index:
            return text
        pattern = re.compile(rf"\b[A-Z][A-Z0-9]{{{min_len - 1},{max_len - 1}}}\b")

        def replace(match: re.Match) -> str:
            token = match.group(0)
            candidates = index.get(token, [])
            if len(candidates) == 1:
                return candidates[0]
            if candidates:
                shortest = min(candidates, key=lambda name: (len(name.split()), len(name)))
                if candidates.count(shortest) == 1:
                    return shortest
            return token

        return pattern.sub(replace, text)

    def extract(self, query: str, threshold: float, phrase_first: bool, resolver: Any, _skip_paren: bool = False) -> Dict[str, Any]:
        paren_groups: List[Dict[str, Any]] = []
        paren_warnings: List[str] = []
        working_query = query

        if not _skip_paren:
            raw_groups, paren_warnings = self._validate_paren_groups(query)
            if not paren_warnings:
                for open_idx, close_idx, inner_text in raw_groups:
                    operator = self._detect_group_operator(inner_text)
                    if operator is None and re.search(r"\band\b|\bor\b", inner_text, re.IGNORECASE):
                        paren_warnings.append("All operators within a group must be the same")
                    group_result = self.extract(inner_text, threshold, phrase_first, resolver, _skip_paren=True)
                    paren_groups.append({
                        "text": inner_text.strip(),
                        "operator": operator,
                        "entities": group_result["entities"],
                        "age_constraints": group_result["age_constraints"],
                        "time_constraints": group_result["time_constraints"],
                    })
                # Strip parenthesised segments from the outer query (right-to-left to preserve indices)
                for open_idx, close_idx, _ in reversed(raw_groups):
                    working_query = working_query[:open_idx] + working_query[close_idx + 1:]
                working_query = re.sub(r"\s+", " ", working_query).strip()

        candidates = self.engine.split_candidates(working_query)
        entities: List[Dict[str, Any]] = []
        seen = set()
        warnings: List[str] = list(paren_warnings)
        global_age_constraints, _ = self.engine.extract_age_constraints(working_query, "query")
        global_time_constraints, _ = self.engine.extract_time_constraints(working_query, "query")
        query_age_constraints = list(global_age_constraints)
        query_time_constraints = list(global_time_constraints)
        entity_age_constraints_all: List[Dict[str, Any]] = []
        entity_time_constraints_all: List[Dict[str, Any]] = []
        has_event_candidate = False

        # Pre-pass: detect whether any candidate includes non-demographic content
        for candidate in candidates:
            defaults_applied = False
            candidate_age_constraints, candidate_without_age = self.engine.extract_age_constraints(candidate, "entity")
            candidate_time_constraints, candidate_without_time = self.engine.extract_time_constraints(
                candidate_without_age, "entity"
            )
            candidate_clean = self.engine.strip_leading_verbs(
                self.engine.clean_candidates(candidate_without_time)
            )
            candidate_normalised = self.engine.apply_demographic_patterns(candidate_clean)
            candidate_normalised = self.engine.apply_mappings(candidate_normalised, "normalise", warnings)
            candidate_normalised = self._expand_acronyms(candidate_normalised, resolver)
            candidate_normalised = self.engine.apply_mappings(candidate_normalised, "bmi", warnings)
            if self.engine.has_non_demographic_content(candidate_normalised):
                has_event_candidate = True
                break

        # Pre-pass: collect any age constraints found in candidate phrases
        for candidate in candidates:
            candidate_age_constraints, candidate_without_age = self.engine.extract_age_constraints(candidate, "entity")
            candidate_time_constraints, candidate_without_time = self.engine.extract_time_constraints(
                candidate_without_age, "entity"
            )
            candidate_clean = self.engine.strip_leading_verbs(
                self.engine.clean_candidates(candidate_without_time)
            )
            candidate_normalised = self.engine.apply_demographic_patterns(candidate_clean)
            candidate_normalised = self.engine.apply_mappings(candidate_normalised, "normalise", warnings)
            candidate_normalised = self._expand_acronyms(candidate_normalised, resolver)
            candidate_normalised = self.engine.apply_mappings(candidate_normalised, "bmi", warnings)

            if not candidate_age_constraints:
                defaults = self.engine.find_demographic_age_default(candidate)
                if defaults:
                    defaults_applied = True
                    candidate_age_constraints.append(
                        {
                            "min": defaults.get("min"),
                            "max": defaults.get("max"),
                            "inclusive": defaults.get("inclusive", True),
                            "scope": "entity",
                        }
                    )

            demographic_only_for_scope = not self.engine.has_non_demographic_content(candidate_normalised)
            demographic_only = (
                demographic_only_for_scope
                and not self.engine.has_demographic_concept(candidate_normalised)
            )

            if candidate_age_constraints:
                if defaults_applied or demographic_only_for_scope or not has_event_candidate:
                    for constraint in candidate_age_constraints:
                        constraint["scope"] = "query"
                    query_age_constraints = self.engine.merge_age_constraints(
                        query_age_constraints, candidate_age_constraints
                    )
                entity_age_constraints_all = self.engine.merge_age_constraints(
                    entity_age_constraints_all, candidate_age_constraints
                )

            if candidate_time_constraints:
                if demographic_only_for_scope or not has_event_candidate:
                    for constraint in candidate_time_constraints:
                        constraint["scope"] = "query"
                    query_time_constraints = self.engine.merge_time_constraints(
                        query_time_constraints, candidate_time_constraints
                    )
                entity_time_constraints_all = self.engine.merge_time_constraints(
                    entity_time_constraints_all, candidate_time_constraints
                )

        for candidate in candidates:
            candidate_age_constraints, candidate_without_age = self.engine.extract_age_constraints(candidate, "entity")
            candidate_time_constraints, candidate_without_time = self.engine.extract_time_constraints(
                candidate_without_age, "entity"
            )
            candidate_clean = self.engine.strip_leading_verbs(
                self.engine.clean_candidates(candidate_without_time)
            )
            candidate_normalised = self.engine.apply_demographic_patterns(candidate_clean)
            candidate_normalised = self.engine.apply_mappings(candidate_normalised, "normalise", warnings)
            candidate_normalised = self._expand_acronyms(candidate_normalised, resolver)

            # Negation
            negated = self.engine.is_negated(candidate)

            print(
                f"Processing candidate: '{candidate}' (clean: '{candidate_clean}', normalised: '{candidate_normalised}'), negated={negated}"
            )

            # Age constraints
            if not candidate_age_constraints:
                defaults = self.engine.find_demographic_age_default(candidate)
                if defaults:
                    candidate_age_constraints.append(
                        {
                            "min": defaults.get("min"),
                            "max": defaults.get("max"),
                            "inclusive": defaults.get("inclusive", True),
                            "scope": "entity",
                        }
                    )

            if entity_age_constraints_all:
                entity_age_constraints = entity_age_constraints_all
            else:
                entity_age_constraints = self.engine.merge_age_constraints(
                    global_age_constraints, candidate_age_constraints
                )

            is_gender_only = (
                self.engine.has_demographic_concept(candidate_normalised)
                and not self.engine.has_non_demographic_content(candidate_normalised)
            )
            if entity_time_constraints_all and not is_gender_only:
                entity_time_constraints = entity_time_constraints_all
            else:
                entity_time_constraints = self.engine.merge_time_constraints(
                    global_time_constraints, candidate_time_constraints
                )

            entity_age_constraints = [
                constraint
                for constraint in entity_age_constraints
                if constraint.get("scope") != "query"
            ]
            entity_time_constraints = [
                constraint
                for constraint in entity_time_constraints
                if constraint.get("scope") != "query"
            ]

            # Unsupported concepts
            unsupported = self.engine.find_unsupported_features(candidate)
            warnings.extend(self.engine.warnings_for_features(unsupported))

            # Skip resolver matching for age-group-only candidates (e.g. "Adults"),
            # but keep demographic concepts (e.g. "Women") so they still resolve.
            if (
                not self.engine.has_non_demographic_content(candidate_normalised)
                and not self.engine.has_demographic_concept(candidate_normalised)
            ):
                continue

            # Resolve concepts
            matches = resolver.resolve(candidate_normalised, threshold, phrase_first=phrase_first)
            index = working_query.lower().find(candidate.lower())

            for match in matches:
                key = (match["concept_id"], candidate.lower(), index)
                if key in seen:
                    continue
                seen.add(key)

                start_idx = index
                end_idx = start_idx + len(candidate)

                entities.append(
                    {
                        "text": candidate,
                        "label": match.get("domain_id"),
                        "start": start_idx,
                        "end": end_idx,
                        "negated": negated,
                        "age_constraints": entity_age_constraints if entity_age_constraints is not None else [],
                        "time_constraints": entity_time_constraints if entity_time_constraints is not None else [],
                        "attributes": match,
                    }
                )

        return {
            "entities": entities,
            "groups": paren_groups,
            "warnings": warnings,
            "age_constraints": query_age_constraints,
            "time_constraints": query_time_constraints,
        }
