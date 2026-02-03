import json

import app as app_module
from app import (
    AGE_OVERRIDES,
    AGE_PATTERNS,
    RULES,
    UNSUPPORTED_PATTERNS,
    apply_mappings,
    extract_age_constraints,
    load_rules,
    split_candidates,
    strip_leading_verbs,
)


def test_rules_splitters_from_config():
    candidates = split_candidates("A and B")
    assert candidates == ["A", "B"]


def test_rules_leading_verbs_from_config():
    cleaned = strip_leading_verbs("diagnosed with asthma")
    assert cleaned == "asthma"


def test_rules_age_patterns_from_config():
    constraints, cleaned = extract_age_constraints("aged 18-30", "entity")
    assert cleaned == ""
    assert constraints
    assert constraints[0]["min"] == 18
    assert constraints[0]["max"] == 30
    assert constraints[0]["inclusive"] is True


def test_rules_unsupported_patterns_from_config():
    assert UNSUPPORTED_PATTERNS["visit"].search("hospital visit") is not None


def test_rules_age_overrides_from_config(tmp_path, monkeypatch):
    rules_path = tmp_path / "rules.json"
    base_rules = {
        "splitters": [",\\s+"],
        "leading_verbs": ["^diagnosed\\s+with\\s+"],
        "age_patterns": [{"pattern": "under\\s+(\\d+)", "op": "<"}],
        "age_overrides": [
            {"pattern": "aged\\s+under\\s+18", "min": None, "max": 18, "inclusive": False}
        ],
        "unsupported_patterns": {"visit": "\\bvisit\\b"},
    }
    rules_path.write_text(json.dumps(base_rules), encoding="utf-8")

    previous_rules = app_module.RULES
    previous_age_patterns = app_module.AGE_PATTERNS
    previous_age_overrides = app_module.AGE_OVERRIDES
    previous_unsupported = app_module.UNSUPPORTED_PATTERNS

    monkeypatch.setenv("RULES_PATH", str(rules_path))
    try:
        rules = load_rules()
        app_module.RULES = rules
        app_module.AGE_PATTERNS = rules["age_patterns"]
        app_module.AGE_OVERRIDES = rules["age_overrides"]
        app_module.UNSUPPORTED_PATTERNS = rules["unsupported_patterns"]

        assert rules["age_overrides"]
        assert rules["age_patterns"]
        assert rules["unsupported_patterns"]

        constraints, cleaned = extract_age_constraints("aged under 18", "entity")
        assert cleaned == ""
        assert constraints
        assert constraints[0]["max"] == 18
        assert constraints[0]["inclusive"] is False
    finally:
        app_module.RULES = previous_rules
        app_module.AGE_PATTERNS = previous_age_patterns
        app_module.AGE_OVERRIDES = previous_age_overrides
        app_module.UNSUPPORTED_PATTERNS = previous_unsupported


def test_mappings_demographic_from_config():
    mapped = apply_mappings("men", "demographic")
    assert "MALE" in mapped


def test_mappings_bmi_from_config():
    warnings = []
    mapped = apply_mappings("BMI over 30", "bmi", warnings)
    assert mapped == "obesity"
    assert any("BMI threshold mapped to obesity" in w for w in warnings)
