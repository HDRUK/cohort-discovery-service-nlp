import json
import os
import re
import string
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple


NEGATION_TERMS = {"no", "not", "without", "never"}


def load_mappings() -> Dict[str, Any]:
    mappings_path = os.getenv("MAPPINGS_PATH", "mappings.json")
    try:
        with open(mappings_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        print(f"[Config] mappings file not found: {mappings_path}")
        sys.exit(1)
    except json.JSONDecodeError as exc:
        print(f"[Config] invalid mappings JSON in {mappings_path}: {exc}")
        sys.exit(1)

    compiled: Dict[str, List[Dict[str, Any]]] = {}
    for entry in data.get("mappings", []):
        group = entry.get("group", "default")
        compiled.setdefault(group, [])
        compiled[group].append(
            {
                "pattern": re.compile(entry["pattern"], re.IGNORECASE),
                "replacement": entry.get("replacement", ""),
                "warning": entry.get("warning"),
                "contains": entry.get("contains", []),
            }
        )
    return compiled


def load_rules() -> Dict[str, Any]:
    rules_path = os.getenv("RULES_PATH", "rules.json")
    try:
        with open(rules_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        print(f"[Config] rules file not found: {rules_path}")
        sys.exit(1)
    except json.JSONDecodeError as exc:
        print(f"[Config] invalid rules JSON in {rules_path}: {exc}")
        sys.exit(1)

    acronym_rules = data.get(
        "acronym_rules",
        {
            "min_len": 2,
            "max_len": 6,
            "token_min_len": 2,
            "token_max_len": 20,
            "stopwords": [],
        },
    )
    env_enabled = os.getenv("ACRONYM_ENABLED")
    if env_enabled is not None:
        acronym_rules["enabled"] = env_enabled.strip().lower() in {"1", "true", "yes", "on"}
    else:
        acronym_rules["enabled"] = True

    return {
        "splitters": data.get("splitters", []),
        "leading_verbs": [re.compile(p, re.IGNORECASE) for p in data.get("leading_verbs", [])],
        "age_patterns": [
            (re.compile(entry["pattern"], re.IGNORECASE), entry["op"])
            for entry in data.get("age_patterns", [])
        ],
        "age_overrides": [
            {
                "pattern": re.compile(entry["pattern"], re.IGNORECASE),
                "min": entry.get("min"),
                "max": entry.get("max"),
                "inclusive": entry.get("inclusive", True),
            }
            for entry in data.get("age_overrides", [])
        ],
        "time_patterns": [
            (re.compile(entry["pattern"], re.IGNORECASE), entry["op"])
            for entry in data.get("time_patterns", [])
        ],
        "demographic_age_defaults": data.get("demographic_age_defaults", {}),
        "demographic_concept_patterns": [
            re.compile(pattern, re.IGNORECASE)
            for pattern in data.get("demographic_concept_patterns", [])
        ],
        "unsupported_patterns": {
            name: {
                "pattern": re.compile(entry.get("pattern", ""), re.IGNORECASE),
                "warning": entry.get("warning", ""),
            }
            for name, entry in data.get("unsupported_patterns", {}).items()
        },
        "acronym_rules": acronym_rules,
    }


class RuleEngine:
    def __init__(self, mappings: Optional[Dict[str, Any]] = None, rules: Optional[Dict[str, Any]] = None):
        self.mappings = mappings or load_mappings()
        self.rules = rules or load_rules()
        self.splitters = self.rules["splitters"]
        self.leading_verbs = self.rules["leading_verbs"]
        self.age_patterns = self.rules["age_patterns"]
        self.age_overrides = self.rules["age_overrides"]
        self.time_patterns = self.rules["time_patterns"]
        self.demographic_age_defaults = self.rules["demographic_age_defaults"]
        self.demographic_concept_patterns = self.rules["demographic_concept_patterns"]
        self.unsupported_patterns = self.rules["unsupported_patterns"]
        self.acronym_rules = self.rules["acronym_rules"]

    def split_candidates(self, text: str) -> List[str]:
        pattern = "|".join(self.splitters)
        candidates = [s.strip() for s in re.split(pattern, text, flags=re.IGNORECASE) if s.strip()]
        print(f"found candidates {candidates}")
        return candidates

    def clean_candidates(self, text: str) -> str:
        punctuation = string.punctuation.replace("-", "")
        text = text.translate(str.maketrans("", "", punctuation))
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def strip_leading_verbs(self, text: str) -> str:
        text = text.strip()
        for pattern in self.leading_verbs:
            text = pattern.sub("", text)
        return text.strip()

    def apply_mappings(self, text: str, group: str, warnings: Optional[List[str]] = None) -> str:
        for entry in self.mappings.get(group, []):
            if entry["contains"]:
                haystack = text.lower()
                if not any(token.lower() in haystack for token in entry["contains"]):
                    continue
            if entry["pattern"].search(text):
                text = entry["pattern"].sub(entry["replacement"], text)
                if warnings is not None and entry.get("warning"):
                    warnings.append(entry["warning"])
        return text.strip()

    def apply_demographic_patterns(self, text: str) -> str:
        return self.apply_mappings(text, "demographic")

    def extract_age_constraints(self, text: str, scope: str) -> Tuple[List[Dict[str, Any]], str]:
        constraints = []
        cleaned = text

        for entry in self.age_overrides:
            for _ in entry["pattern"].finditer(cleaned):
                constraints.append(
                    {
                        "min": entry.get("min"),
                        "max": entry.get("max"),
                        "inclusive": entry.get("inclusive", True),
                        "scope": scope,
                    }
                )
            cleaned = entry["pattern"].sub("", cleaned)

        for pattern, op in self.age_patterns:
            for m in pattern.finditer(cleaned):
                if op == "<":
                    max_age = int(m.group(1))
                    constraints.append({"min": None, "max": max_age, "inclusive": False, "scope": scope})
                elif op == ">":
                    min_age = int(m.group(1))
                    constraints.append({"min": min_age, "max": None, "inclusive": False, "scope": scope})
                elif op == ">=":
                    min_age = int(m.group(1))
                    constraints.append({"min": min_age, "max": None, "inclusive": True, "scope": scope})
                elif op == "range":
                    min_age = int(m.group(1))
                    max_age = int(m.group(2))
                    if min_age > max_age:
                        min_age, max_age = max_age, min_age
                    constraints.append({"min": min_age, "max": max_age, "inclusive": True, "scope": scope})

            cleaned = pattern.sub("", cleaned)

        return constraints, cleaned.strip()

    def extract_time_constraints(self, text: str, scope: str) -> Tuple[List[Dict[str, Any]], str]:
        constraints = []
        cleaned = text

        for pattern, op in self.time_patterns:
            for m in pattern.finditer(cleaned):
                if op == "last":
                    value = int(m.group(1))
                    unit = m.group(2).lower()
                    now = datetime.utcnow()
                    if unit.startswith("year"):
                        start = now - timedelta(days=value * 365)
                    elif unit.startswith("month"):
                        start = now - timedelta(days=value * 30)
                    else:
                        start = now
                    constraints.append({"from": start.isoformat(), "to": now.isoformat(), "scope": scope})
            cleaned = pattern.sub("", cleaned)

        return constraints, cleaned.strip()

    def merge_age_constraints(
        self, primary: List[Dict[str, Any]], secondary: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        merged: List[Dict[str, Any]] = []
        seen = set()
        for entry in primary + secondary:
            key = (entry.get("min"), entry.get("max"), entry.get("inclusive"), entry.get("scope"))
            if key in seen:
                continue
            seen.add(key)
            merged.append(entry)
        return merged

    def merge_time_constraints(
        self, primary: List[Dict[str, Any]], secondary: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        merged: List[Dict[str, Any]] = []
        seen = set()
        for entry in primary + secondary:
            key = (entry.get("from"), entry.get("to"), entry.get("scope"))
            if key in seen:
                continue
            seen.add(key)
            merged.append(entry)
        return merged

    def has_non_demographic_content(self, text: str) -> bool:
        text = re.sub(r"\b(MALE|FEMALE|CHILD)\b", "", text, flags=re.IGNORECASE)
        for term in self.demographic_age_defaults.keys():
            text = re.sub(rf"\b{re.escape(term)}s?\b", "", text, flags=re.IGNORECASE)
        text = re.sub(
            r"\b(who|were|are|is|aged|age|under|over|when|they|he|she|people|patients|with|the|a|an)\b",
            "",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(r"\s+", " ", text).strip()
        return bool(text)

    def find_demographic_age_default(self, text: str) -> Optional[Dict[str, Any]]:
        for term, defaults in self.demographic_age_defaults.items():
            if re.search(rf"\b{re.escape(term)}s?\b", text, re.IGNORECASE):
                return defaults
        return None

    def has_demographic_concept(self, text: str) -> bool:
        return any(pattern.search(text) for pattern in self.demographic_concept_patterns)

    def find_unsupported_features(self, text: str) -> List[str]:
        return [
            name
            for name, entry in self.unsupported_patterns.items()
            if entry["pattern"].search(text)
        ]

    def warnings_for_features(self, features: List[str]) -> List[str]:
        warnings = []
        for feature in features:
            warning = self.unsupported_patterns.get(feature, {}).get("warning")
            if warning:
                warnings.append(warning)
        return warnings

    def is_negated(self, text: str) -> bool:
        for term in NEGATION_TERMS:
            if re.search(rf"\b{re.escape(term)}\b", text, re.IGNORECASE):
                print(f"Negation term matched: '{term}' in '{text}'")
                return True
        return False

    def build_acronym_index(self, concepts: List[Dict[str, Any]]) -> Dict[str, List[str]]:
        rules = self.acronym_rules
        if not rules.get("enabled", True):
            return {}
        min_len = int(rules.get("min_len", 2))
        max_len = int(rules.get("max_len", 6))
        token_min_len = int(rules.get("token_min_len", 2))
        token_max_len = int(rules.get("token_max_len", 20))
        stopwords = {w.lower() for w in rules.get("stopwords", [])}
        index: Dict[str, List[str]] = {}

        for concept in concepts:
            name = concept.get("concept_name") or concept.get("description") or ""
            if not name:
                continue
            tokens = re.findall(r"[A-Za-z0-9]+", name)
            initials = []
            for token in tokens:
                lower = token.lower()
                if lower in stopwords:
                    continue
                if not (token_min_len <= len(token) <= token_max_len):
                    continue
                initials.append(token[0].upper())
            acronym = "".join(initials)
            if not (min_len <= len(acronym) <= max_len):
                continue
            index.setdefault(acronym, [])
            if name not in index[acronym]:
                index[acronym].append(name)
        return index


DEFAULT_ENGINE = RuleEngine()
