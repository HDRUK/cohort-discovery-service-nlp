import calendar
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

AGE_MIN = 0
AGE_MAX = 120

SEX_CONCEPTS = {
    "male": {"concept_id": 8507, "name": "Male", "category": "Gender"},
    "female": {"concept_id": 8532, "name": "Female", "category": "Gender"},
}

RACE_CONCEPTS = {
    "black": {"concept_id": 8516, "name": "Black or African American", "category": "Race"},
    "white": {"concept_id": 8527, "name": "White", "category": "Race"},
    "asian": {"concept_id": 8515, "name": "Asian", "category": "Race"},
    "american_indian": {
        "concept_id": 8657,
        "name": "American Indian or Alaska Native",
        "category": "Race",
    },
    "pacific_islander": {
        "concept_id": 8557,
        "name": "Native Hawaiian or Other Pacific Islander",
        "category": "Race",
    },
}

DEATH_OPTIONS = {
    "yes": {"value": 1, "label": "Recorded"},
    "no": {"value": 0, "label": "Not recorded"},
}

QUERY_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "age": {
            "type": "array",
            "items": {"type": "integer"},
            "minItems": 2,
            "maxItems": 2,
        },
        "sex": {
            "type": "array",
            "items": {"type": "string", "enum": ["male", "female"]},
        },
        "race": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": ["black", "white", "asian", "american_indian", "pacific_islander"],
            },
        },
        "death": {"type": "string", "enum": ["any", "yes", "no"]},
        "op": {"type": "string", "enum": ["and", "or", "followed_by"]},
        "last_months": {"type": "integer"},
        "rules": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "term": {"type": "string"},
                    "not": {"type": "boolean"},
                    "op": {"type": "string", "enum": ["and", "or", "followed_by"]},
                    "terms": {"type": "array", "items": {"type": "string"}},
                    "last_months": {"type": "integer"},
                    "age_min": {"type": "integer"},
                    "age_max": {"type": "integer"},
                    "value_min": {"type": "number"},
                    "value_max": {"type": "number"},
                },
            },
        },
    },
    "required": ["age", "sex", "race", "death", "op", "rules"],
}

SYSTEM_PROMPT = """You convert a researcher's plain-English cohort description into a compact query plan.

You never invent OMOP concept ids or codes. You only identify clinical search terms and how they combine.

"age": [min, max]. adults [18,120]. children/paediatric [0,17]. infants/babies [0,1]. teenagers/teens/adolescents [13,19]. elderly/seniors [65,120]. "over 50" [50,120]. "under 30" [0,30]. "aged 40-60" [40,60]. No age mentioned: [0,120].
"sex": ["female"] for women/female, ["male"] for men/male, [] if unmentioned.
"race": ["black"], ["white"], ["asian"], ["american_indian"] or ["pacific_islander"]. [] if unmentioned.
"death": "yes" for died/death/deceased/mortality/fatal/non-survivors/passed away. "no" for alive/survived/still living/not deceased. "any" otherwise.
"op": "or" if the query says or/either. "followed_by" if the query describes one event happening after another ("later developed", "then switched", "who subsequently"). Otherwise "and".
"rules": one entry per clinical term, lower case, as a clinician would type it into a concept search box. Strip leading verbs and "with"/"on"/"taking"/"diagnosed with".

A rule is {"term":"cancer"}. Add "not":true only for an explicitly excluded term ("without asthma", "no history of stroke"). For an explicit sub-expression such as a parenthesised list use {"op":"or","terms":["insulin glargine","insulin detemir"]}.

Deciding where an age belongs is the most important judgement you make. There are two different questions and they give different cohorts.

TOP-LEVEL "age" - the patient's age NOW. Use this when the age describes the person rather than the event. The giveaway is that the age sits inside the noun phrase naming the patients, and the condition is attached separately with "with" or "who have":
  "adults with asthma"                      -> age [18,120], rules [{"term":"asthma"}]
  "women aged 18-45 with endometriosis"     -> age [18,45],  rules [{"term":"endometriosis"}]
  "people aged 65+ with hypertension"       -> age [65,120], rules [{"term":"hypertension"}]
  "men over 60 with cancer"                 -> age [60,120], rules [{"term":"cancer"}]

RULE "age_min"/"age_max" - the patient's age WHEN THE EVENT HAPPENED. Use this when the age is tied to the event by a temporal or conditional clause. The giveaways are "when", "at the time of", "at diagnosis", "at onset", "by the age of", or an age that follows the event rather than the patient:
  "women who were under 60 when they suffered a hip fracture"  -> age [0,120], rules [{"term":"hip fracture","age_max":60}]
  "patients diagnosed with diabetes before the age of 40"      -> age [0,120], rules [{"term":"diabetes","age_max":40}]
  "people who had a stroke aged over 70"                       -> age [0,120], rules [{"term":"stroke","age_min":70}]
  "cancer diagnosed at 50 or older"                            -> age [0,120], rules [{"term":"cancer","age_min":50}]

Test it by asking which the query means: a 70-year-old who broke a hip at 55 matches "women who were under 60 when they suffered a hip fracture" but does not match "women under 60 with a hip fracture". If the query genuinely does not say, treat it as the patient's current age and use the top-level "age".

An age never appears in both places for the same constraint.

A numeric threshold on a measurement belongs on the rule as "value_min"/"value_max". "BMI over 30" is {"term":"bmi","value_min":30}. "HbA1c above 75 mmol/mol" is {"term":"hba1c","value_min":75}. "eGFR below 45" is {"term":"egfr","value_max":45}. Never put the number in the search term.

A time window is never a search term. "in the last 2 years" is "last_months":24, "in the past 6 months" is "last_months":6, "within the last year" is "last_months":12. Put "last_months" on the rule the window describes - "diagnosed with cancer in the last 2 years" is {"term":"cancer","last_months":24}. Put it at the top level only when it applies to the whole query. Never emit a term like "diagnosed in last 2 years".

Omit every field you do not need. Never put age, sex or death in "rules" - "died of covid" is death "yes" plus {"term":"covid"}.

Query: "adults with cancer or diabetes"
{"age":[18,120],"sex":[],"race":[],"death":"any","op":"or","rules":[{"term":"cancer"},{"term":"diabetes"}]}

Query: "women over 60 with type 2 diabetes on insulin (glargine or detemir)"
{"age":[60,120],"sex":["female"],"race":[],"death":"any","op":"and","rules":[{"term":"type 2 diabetes"},{"op":"or","terms":["insulin glargine","insulin detemir"]}]}

Query: "teenagers who died of covid, without asthma"
{"age":[13,19],"sex":[],"race":[],"death":"yes","op":"and","rules":[{"term":"covid"},{"term":"asthma","not":true}]}

Query: "females who were diagnosed with cancer in the last 2 years"
{"age":[0,120],"sex":["female"],"race":[],"death":"any","op":"and","rules":[{"term":"cancer","last_months":24}]}

Query: "women who were under 60 when they suffered a hip fracture"
{"age":[0,120],"sex":["female"],"race":[],"death":"any","op":"and","rules":[{"term":"hip fracture","age_max":60}]}

Query: "women aged 18-45 with endometriosis"
{"age":[18,45],"sex":["female"],"race":[],"death":"any","op":"and","rules":[{"term":"endometriosis"}]}

Query: "black men over 60 with BMI over 30 who later developed heart failure"
{"age":[60,120],"sex":["male"],"race":["black"],"death":"any","op":"followed_by","rules":[{"term":"bmi","value_min":30},{"term":"heart failure"}]}
"""


def _node_id() -> str:
    return str(uuid.uuid4())


def _months_ago(months: int) -> str:
    now = datetime.now(timezone.utc)
    total = now.year * 12 + (now.month - 1) - int(months)
    year, month_index = divmod(total, 12)
    month = month_index + 1
    day = min(now.day, calendar.monthrange(year, month)[1])
    return now.replace(year=year, month=month, day=day).isoformat()


def _time_constraint(last_months: Any) -> Optional[List[Optional[str]]]:
    if last_months is None:
        return None
    months = int(last_months)
    if months <= 0:
        return None
    return [_months_ago(months), datetime.now(timezone.utc).isoformat()]


def _bounds(low: Any, high: Any) -> Optional[List[Optional[float]]]:
    if low is None and high is None:
        return None
    return [low, high]


def _single_sided_operator(low: Any, high: Any) -> Optional[str]:
    if low is not None and high is None:
        return "\u2265"
    if low is None and high is not None:
        return "<"
    return None


def _value_operator(low: Any, high: Any) -> Optional[str]:
    if low is not None and high is not None:
        return "\u2194"
    return _single_sided_operator(low, high)


def _apply_constraints(node: Dict[str, Any], item: Dict[str, Any]) -> Dict[str, Any]:
    time_constraint = _time_constraint(item.get("last_months"))
    if time_constraint:
        node["timeConstraint"] = time_constraint

    age = _bounds(item.get("age_min"), item.get("age_max"))
    if age:
        node["ageConstraint"] = age
        operator = _single_sided_operator(age[0], age[1])
        if operator:
            node["ageConstraintOperator"] = operator

    value = _bounds(item.get("value_min"), item.get("value_max"))
    if value:
        node["valueAsNumber"] = value
        operator = _value_operator(value[0], value[1])
        if operator:
            node["valueAsNumberOperator"] = operator

    return node


def _leaf(term: str, item: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    item = item or {}
    leaf: Dict[str, Any] = {
        "id": _node_id(),
        "exclude": bool(item.get("not", False)),
        "searchTerm": term.strip(),
        "rule": {"concept": None},
    }
    return _apply_constraints(leaf, item)


def _operator(combinator: str) -> Dict[str, Any]:
    return {"id": _node_id(), "combinator": combinator, "exclude": False}


def _interleave(nodes: List[Dict[str, Any]], combinator: str) -> List[Dict[str, Any]]:
    interleaved: List[Dict[str, Any]] = []
    for node in nodes:
        if interleaved:
            interleaved.append(_operator(combinator))
        interleaved.append(node)
    return interleaved


def _group(terms: List[str], combinator: str, item: Dict[str, Any]) -> Dict[str, Any]:
    leaves = [_leaf(term) for term in terms if str(term).strip()]
    group: Dict[str, Any] = {
        "id": _node_id(),
        "exclude": False,
        "rules": _interleave(leaves, combinator),
    }
    return _apply_constraints(group, item)


def _age(age: Any) -> List[int]:
    values = age if isinstance(age, list) and len(age) == 2 else [None, None]
    low = AGE_MIN if values[0] is None else max(AGE_MIN, int(values[0]))
    high = AGE_MAX if values[1] is None else min(AGE_MAX, int(values[1]))
    if low > high:
        return [AGE_MIN, AGE_MAX]
    return [low, high]


def _sex(values: Any) -> List[Dict[str, Any]]:
    sex: List[Dict[str, Any]] = []
    seen = set()
    for value in values or []:
        concept = SEX_CONCEPTS.get(str(value).strip().lower())
        if concept and concept["concept_id"] not in seen:
            seen.add(concept["concept_id"])
            sex.append(dict(concept))
    return sex


def _race(values: Any) -> List[Dict[str, Any]]:
    race: List[Dict[str, Any]] = []
    seen = set()
    for value in values or []:
        concept = RACE_CONCEPTS.get(str(value).strip().lower())
        if concept and concept["concept_id"] not in seen:
            seen.add(concept["concept_id"])
            race.append(dict(concept))
    return race


def _death(value: Any) -> Optional[Dict[str, Any]]:
    return DEATH_OPTIONS.get(str(value or "any").strip().lower())


def _describe_age(low: int, high: int) -> str:
    if low > AGE_MIN and high < AGE_MAX:
        return f"between {low} and {high}"
    if low > AGE_MIN:
        return f">= {low}"
    return f"<= {high}"


def interpretation_warnings(plan: Dict[str, Any]) -> List[str]:
    """Name the reading taken wherever the query admitted more than one."""
    warnings: List[str] = []

    age = _age(plan.get("age"))
    if age != [AGE_MIN, AGE_MAX]:
        warnings.append(
            f"Age interpreted as the patient's current age {_describe_age(*age)}. "
            "Please modify from the query builder if needed."
        )

    for item in plan.get("rules") or []:
        bounds = _bounds(item.get("age_min"), item.get("age_max"))
        if not bounds:
            continue
        label = str(item.get("term") or "").strip() or "the group"
        low = AGE_MIN if bounds[0] is None else bounds[0]
        high = AGE_MAX if bounds[1] is None else bounds[1]
        warnings.append(
            f'Age for "{label}" interpreted as the patient\'s age when the event was '
            f"recorded {_describe_age(low, high)}, not their current age. "
            "Please modify from the query builder if needed."
        )

    return warnings


def plan_to_tree(plan: Dict[str, Any]) -> Dict[str, Any]:
    nodes: List[Dict[str, Any]] = []
    for item in plan.get("rules") or []:
        terms = [t for t in (item.get("terms") or []) if str(t).strip()]
        if terms:
            nodes.append(_group(terms, item.get("op") or "and", item))
            continue
        term = str(item.get("term") or "").strip()
        if term:
            nodes.append(_leaf(term, item))

    return {
        "id": _node_id(),
        "rules": _interleave(nodes, plan.get("op") or "and"),
        "demographics": {
            "age": _age(plan.get("age")),
            "sex": _sex(plan.get("sex")),
            "race": _race(plan.get("race")),
            "death": _death(plan.get("death")),
        },
        "constraints": {
            "ageConstraint": [None, None],
            "timeConstraint": _time_constraint(plan.get("last_months"))
            or [None, None],
        },
        "warnings": [],
        "valid": True,
    }
