import re
from typing import Any, Dict, List

from rules_engine import RuleEngine


class QueryParser:
    def __init__(self, engine: RuleEngine):
        self.engine = engine

    def extract(self, query: str, threshold: float, phrase_first: bool, resolver: Any) -> Dict[str, Any]:
        candidates = self.engine.split_candidates(query)
        entities: List[Dict[str, Any]] = []
        seen = set()
        warnings: List[str] = []
        global_age_constraints, _ = self.engine.extract_age_constraints(query, "query")
        global_time_constraints, _ = self.engine.extract_time_constraints(query, "query")
        entity_age_constraints_all: List[Dict[str, Any]] = []
        entity_time_constraints_all: List[Dict[str, Any]] = []
        has_event_candidate = False

        # Pre-pass: detect whether any candidate includes non-demographic content
        for candidate in candidates:
            candidate_age_constraints, candidate_without_age = self.engine.extract_age_constraints(candidate, "entity")
            candidate_time_constraints, candidate_without_time = self.engine.extract_time_constraints(
                candidate_without_age, "entity"
            )
            candidate_clean = self.engine.strip_leading_verbs(
                self.engine.clean_candidates(candidate_without_time)
            )
            candidate_normalised = self.engine.apply_demographic_patterns(candidate_clean)
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
            candidate_normalised = self.engine.apply_mappings(candidate_normalised, "bmi", warnings)

            if re.search(r"\bCHILD\b", candidate_normalised, re.IGNORECASE):
                if not candidate_age_constraints:
                    candidate_age_constraints.append(
                        {"min": None, "max": 18, "inclusive": False, "scope": "entity"}
                    )

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

            if candidate_age_constraints:
                if not has_event_candidate:
                    for constraint in candidate_age_constraints:
                        constraint["scope"] = "query"
                entity_age_constraints_all = self.engine.merge_age_constraints(
                    entity_age_constraints_all, candidate_age_constraints
                )

            if candidate_time_constraints:
                if not has_event_candidate:
                    for constraint in candidate_time_constraints:
                        constraint["scope"] = "query"
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

            # Negation
            negated = self.engine.is_negated(candidate)

            print(
                f"Processing candidate: '{candidate}' (clean: '{candidate_clean}', normalised: '{candidate_normalised}'), negated={negated}"
            )

            # Age constraints
            if re.search(r"\bCHILD\b", candidate_normalised, re.IGNORECASE):
                if not candidate_age_constraints:
                    candidate_age_constraints.append(
                        {"min": None, "max": 18, "inclusive": False, "scope": "entity"}
                    )
                candidate_normalised = re.sub(r"\bCHILD\b", "", candidate_normalised, flags=re.IGNORECASE)
                candidate_normalised = re.sub(r"\s+", " ", candidate_normalised).strip()

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

            if entity_time_constraints_all:
                entity_time_constraints = entity_time_constraints_all
            else:
                entity_time_constraints = self.engine.merge_time_constraints(
                    global_time_constraints, candidate_time_constraints
                )

            # Unsupported concepts
            unsupported = self.engine.find_unsupported_features(candidate)

            for feature in unsupported:
                warnings.append(f"{feature.capitalize()}-based filtering is not currently supported.")

            # Skip resolver matching for age-group-only candidates (e.g. "Adults"),
            # but keep demographic concepts (e.g. "Women") so they still resolve.
            if (
                not self.engine.has_non_demographic_content(candidate_normalised)
                and not self.engine.has_demographic_concept(candidate_normalised)
            ):
                continue

            # Resolve concepts
            matches = resolver.resolve(candidate_normalised, threshold, phrase_first=phrase_first)
            index = query.lower().find(candidate.lower())

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

        return {"entities": entities, "warnings": warnings}
