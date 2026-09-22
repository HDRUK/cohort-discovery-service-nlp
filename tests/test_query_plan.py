from llm.query_plan import plan_to_tree

CANCER_OR_DIABETES = {
    "age": [18, 120],
    "sex": [],
    "death": "any",
    "op": "or",
    "rules": [{"term": "cancer"}, {"term": "diabetes"}],
}

WITH_GROUP = {
    "age": [60, 120],
    "sex": ["female"],
    "death": "any",
    "op": "and",
    "rules": [
        {"term": "type 2 diabetes"},
        {"op": "or", "terms": ["insulin glargine", "insulin detemir"]},
    ],
}


def _all_ids(node, ids):
    ids.append(node["id"])
    for child in node.get("rules") or []:
        _all_ids(child, ids)
    return ids


def test_operators_are_interleaved_between_leaves():
    tree = plan_to_tree(CANCER_OR_DIABETES)

    rules = tree["rules"]
    assert len(rules) == 3
    assert rules[0]["searchTerm"] == "cancer"
    assert rules[1]["combinator"] == "or"
    assert rules[2]["searchTerm"] == "diabetes"


def test_every_concept_starts_blank():
    tree = plan_to_tree(CANCER_OR_DIABETES)

    leaves = [node for node in tree["rules"] if "rule" in node]
    assert len(leaves) == 2
    assert all(leaf["rule"]["concept"] is None for leaf in leaves)


def test_every_node_has_a_distinct_id():
    ids = _all_ids(plan_to_tree(WITH_GROUP), [])

    assert len(ids) == len(set(ids))
    assert all(ids)


def test_adults_becomes_a_clamped_age_range():
    tree = plan_to_tree(CANCER_OR_DIABETES)

    assert tree["demographics"]["age"] == [18, 120]
    assert tree["demographics"]["sex"] == []
    assert tree["demographics"]["race"] == []


def test_sex_maps_to_omop_gender_concepts():
    tree = plan_to_tree(WITH_GROUP)

    assert tree["demographics"]["sex"] == [
        {"concept_id": 8532, "name": "Female", "category": "Gender"}
    ]


def test_age_is_clamped_to_zero_and_one_hundred_and_twenty():
    tree = plan_to_tree({"age": [-5, 999], "sex": [], "rules": []})

    assert tree["demographics"]["age"] == [0, 120]


def test_inverted_age_range_falls_back_to_the_full_range():
    tree = plan_to_tree({"age": [90, 20], "sex": [], "rules": []})

    assert tree["demographics"]["age"] == [0, 120]


def test_group_becomes_a_nested_group_node():
    tree = plan_to_tree(WITH_GROUP)

    rules = tree["rules"]
    assert rules[1]["combinator"] == "and"

    group = rules[2]
    assert "rules" in group
    assert [node.get("searchTerm") for node in group["rules"]] == [
        "insulin glargine",
        None,
        "insulin detemir",
    ]
    assert group["rules"][1]["combinator"] == "or"


def test_negated_term_sets_exclude():
    tree = plan_to_tree(
        {"age": [0, 120], "sex": [], "op": "and",
         "rules": [{"term": "asthma", "not": True}]}
    )

    assert tree["rules"][0]["exclude"] is True


def test_empty_search_terms_are_dropped():
    tree = plan_to_tree(
        {"age": [0, 120], "sex": [], "op": "and",
         "rules": [{"term": "   "}, {"op": "or", "terms": []}]}
    )

    assert tree["rules"] == []


def test_envelope_matches_the_parse_query_shape():
    tree = plan_to_tree(CANCER_OR_DIABETES)

    assert tree["valid"] is True
    assert tree["warnings"] == []
    assert tree["constraints"] == {
        "ageConstraint": [None, None],
        "timeConstraint": [None, None],
    }


def test_death_yes_maps_to_the_recorded_option():
    tree = plan_to_tree({"age": [0, 120], "sex": [], "death": "yes", "rules": []})

    assert tree["demographics"]["death"] == {"value": 1, "label": "Recorded"}


def test_death_no_maps_to_the_not_recorded_option():
    tree = plan_to_tree({"age": [0, 120], "sex": [], "death": "no", "rules": []})

    assert tree["demographics"]["death"] == {"value": 0, "label": "Not recorded"}


def test_death_any_leaves_the_block_empty():
    tree = plan_to_tree({"age": [0, 120], "sex": [], "death": "any", "rules": []})

    assert tree["demographics"]["death"] is None


def test_missing_death_is_treated_as_any():
    tree = plan_to_tree({"age": [0, 120], "sex": [], "rules": []})

    assert tree["demographics"]["death"] is None


def test_teenager_age_band_survives_conversion():
    tree = plan_to_tree({"age": [13, 19], "sex": [], "death": "yes",
                         "op": "and", "rules": [{"term": "covid"}]})

    assert tree["demographics"]["age"] == [13, 19]
    assert tree["rules"][0]["searchTerm"] == "covid"


def test_missing_age_falls_back_to_the_full_range():
    tree = plan_to_tree({"sex": [], "rules": []})

    assert tree["demographics"]["age"] == [0, 120]


def test_last_months_on_a_rule_becomes_a_leaf_time_constraint():
    tree = plan_to_tree(
        {"age": [0, 120], "sex": ["female"], "op": "and",
         "rules": [{"term": "cancer", "last_months": 24}]}
    )

    leaf = tree["rules"][0]
    assert leaf["timeConstraint"][0].startswith(str(_expected_year(24)))
    assert leaf["timeConstraint"][1].startswith(str(_expected_year(0)))


def _expected_year(months):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    return divmod(now.year * 12 + (now.month - 1) - months, 12)[0]


def test_top_level_last_months_becomes_a_root_constraint():
    tree = plan_to_tree(
        {"age": [0, 120], "sex": [], "op": "and", "last_months": 6, "rules": []}
    )

    assert tree["constraints"]["timeConstraint"][0] is not None
    assert tree["constraints"]["timeConstraint"][1] is not None


def test_rules_without_a_window_have_no_time_constraint():
    tree = plan_to_tree(
        {"age": [0, 120], "sex": [], "op": "or",
         "rules": [{"term": "cancer"}, {"term": "diabetes"}]}
    )

    assert all("timeConstraint" not in n for n in tree["rules"] if "rule" in n)
    assert tree["constraints"]["timeConstraint"] == [None, None]


def test_zero_or_negative_months_is_ignored():
    tree = plan_to_tree(
        {"age": [0, 120], "sex": [], "op": "and",
         "rules": [{"term": "cancer", "last_months": 0}]}
    )

    assert "timeConstraint" not in tree["rules"][0]


def test_group_can_carry_a_time_constraint():
    tree = plan_to_tree(
        {"age": [0, 120], "sex": [], "op": "and",
         "rules": [{"op": "or", "terms": ["a", "b"], "last_months": 12}]}
    )

    assert tree["rules"][0]["timeConstraint"][0] is not None


def test_race_maps_to_omop_race_concepts():
    tree = plan_to_tree({"age": [0, 120], "sex": [], "race": ["black"], "rules": []})

    assert tree["demographics"]["race"] == [
        {"concept_id": 8516, "name": "Black or African American", "category": "Race"}
    ]


def test_unknown_race_is_dropped():
    tree = plan_to_tree({"age": [0, 120], "sex": [], "race": ["martian"], "rules": []})

    assert tree["demographics"]["race"] == []


def test_rule_age_becomes_an_age_constraint_not_demographics():
    tree = plan_to_tree(
        {"age": [0, 120], "sex": ["female"], "op": "and",
         "rules": [{"term": "hip fracture", "age_max": 60}]}
    )

    assert tree["demographics"]["age"] == [0, 120]
    assert tree["rules"][0]["ageConstraint"] == [None, 60]
    assert tree["rules"][0]["ageConstraintOperator"] == "<"


def test_lower_bound_age_constraint_uses_the_gte_operator():
    tree = plan_to_tree(
        {"age": [0, 120], "sex": [], "op": "and",
         "rules": [{"term": "stroke", "age_min": 40}]}
    )

    assert tree["rules"][0]["ageConstraint"] == [40, None]
    assert tree["rules"][0]["ageConstraintOperator"] == "≥"


def test_value_threshold_becomes_value_as_number():
    tree = plan_to_tree(
        {"age": [0, 120], "sex": [], "op": "and",
         "rules": [{"term": "bmi", "value_min": 30}]}
    )

    assert tree["rules"][0]["valueAsNumber"] == [30, None]
    assert tree["rules"][0]["valueAsNumberOperator"] == "≥"


def test_value_upper_bound_uses_the_less_than_operator():
    tree = plan_to_tree(
        {"age": [0, 120], "sex": [], "op": "and",
         "rules": [{"term": "egfr", "value_max": 45}]}
    )

    assert tree["rules"][0]["valueAsNumber"] == [None, 45]
    assert tree["rules"][0]["valueAsNumberOperator"] == "<"


def test_value_range_uses_the_between_operator():
    tree = plan_to_tree(
        {"age": [0, 120], "sex": [], "op": "and",
         "rules": [{"term": "hba1c", "value_min": 48, "value_max": 75}]}
    )

    assert tree["rules"][0]["valueAsNumber"] == [48, 75]
    assert tree["rules"][0]["valueAsNumberOperator"] == "↔"


def test_followed_by_is_emitted_as_a_combinator():
    tree = plan_to_tree(
        {"age": [0, 120], "sex": [], "op": "followed_by",
         "rules": [{"term": "pcos"}, {"term": "type 2 diabetes"}]}
    )

    assert tree["rules"][1]["combinator"] == "followed_by"


def test_rules_without_constraints_stay_clean():
    tree = plan_to_tree(
        {"age": [0, 120], "sex": [], "op": "or", "rules": [{"term": "asthma"}]}
    )

    leaf = tree["rules"][0]
    assert "ageConstraint" not in leaf
    assert "valueAsNumber" not in leaf
    assert "timeConstraint" not in leaf
