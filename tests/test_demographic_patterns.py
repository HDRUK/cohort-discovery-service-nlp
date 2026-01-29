from app import apply_demographic_patterns


def test_apply_demographic_patterns_replaces_male_terms():
    text = "men with asthma"
    assert apply_demographic_patterns(text) == "MALE with asthma"

    text = "males over 24 with chronic kidney disease"
    assert apply_demographic_patterns(text) == "MALE over 24 with chronic kidney disease"


def test_apply_demographic_patterns_replaces_female_terms():
    text = "women with diabetes"
    assert apply_demographic_patterns(text) == "FEMALE with diabetes"

    text = "females with asthma"
    assert apply_demographic_patterns(text) == "FEMALE with asthma"
