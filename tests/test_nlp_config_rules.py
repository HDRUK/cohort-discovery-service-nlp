import json

from rules_engine import RuleEngine, load_mappings, load_rules


def test_rules_splitters_from_config():
    engine = RuleEngine()
    candidates = engine.split_candidates("A and B")
    assert candidates == ["A", "B"]


def test_rules_leading_verbs_from_config():
    engine = RuleEngine()
    cleaned = engine.strip_leading_verbs("diagnosed with asthma")
    assert cleaned == "asthma"


def test_rules_age_patterns_from_config():
    engine = RuleEngine()
    constraints, cleaned = engine.extract_age_constraints("aged 18-30", "entity")
    assert cleaned == ""
    assert constraints
    assert constraints[0]["min"] == 18
    assert constraints[0]["max"] == 30
    assert constraints[0]["inclusive"] is True


def test_rules_unsupported_patterns_from_config():
    engine = RuleEngine()
    assert engine.unsupported_patterns["visit"]["pattern"].search("hospital visit") is not None


def test_rules_age_overrides_from_config(tmp_path, monkeypatch):
    rules_path = tmp_path / "rules.json"
    base_rules = {
        "splitters": [",\\s+"],
        "leading_verbs": ["^diagnosed\\s+with\\s+"],
        "age_patterns": [{"pattern": "under\\s+(\\d+)", "op": "<"}],
        "age_overrides": [
            {"pattern": "aged\\s+under\\s+18", "min": None, "max": 18, "inclusive": False}
        ],
        "unsupported_patterns": {"visit": {"pattern": "\\bvisit\\b", "warning": "Visit warning"}},
    }
    rules_path.write_text(json.dumps(base_rules), encoding="utf-8")

    monkeypatch.setenv("RULES_PATH", str(rules_path))
    rules = load_rules()
    engine = RuleEngine(mappings=load_mappings(), rules=rules)

    assert engine.age_overrides
    assert engine.age_patterns
    assert engine.unsupported_patterns

    constraints, cleaned = engine.extract_age_constraints("aged under 18", "entity")
    assert cleaned == ""
    assert constraints
    assert constraints[0]["max"] == 18
    assert constraints[0]["inclusive"] is False


def test_mappings_demographic_from_config():
    engine = RuleEngine()
    mapped = engine.apply_mappings("men", "demographic")
    assert "MALE" in mapped


def test_mappings_bmi_from_config():
    warnings = []
    engine = RuleEngine()
    mapped = engine.apply_mappings("BMI over 30", "bmi", warnings)
    assert mapped == "obesity"
    assert any("BMI threshold mapped to obesity" in w for w in warnings)
