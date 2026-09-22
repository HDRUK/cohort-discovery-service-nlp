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
        "death": {"type": "string", "enum": ["any", "yes", "no"]},
        "op": {"type": "string", "enum": ["and", "or"]},
        "last_months": {"type": "integer"},
        "rules": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "term": {"type": "string"},
                    "not": {"type": "boolean"},
                    "op": {"type": "string", "enum": ["and", "or"]},
                    "terms": {"type": "array", "items": {"type": "string"}},
                    "last_months": {"type": "integer"},
                },
            },
        },
    },
    "required": ["age", "sex", "death", "op", "rules"],
}

SYSTEM_PROMPT = """You convert a researcher's plain-English cohort description into a compact query plan.

You never invent OMOP concept ids or codes. You only identify clinical search terms and how they combine.

"age": [min, max]. adults [18,120]. children/paediatric [0,17]. infants/babies [0,1]. teenagers/teens/adolescents [13,19]. elderly/seniors [65,120]. "over 50" [50,120]. "under 30" [0,30]. "aged 40-60" [40,60]. No age mentioned: [0,120].
"sex": ["female"] for women/female, ["male"] for men/male, [] if unmentioned.
"death": "yes" for died/death/deceased/mortality/fatal/non-survivors/passed away. "no" for alive/survived/still living/not deceased. "any" otherwise.
"op": "or" if the query says or/either, else "and".
"rules": one entry per clinical term, lower case, as a clinician would type it into a concept search box. Strip leading verbs and "with"/"on"/"taking"/"diagnosed with".

A rule is {"term":"cancer"}. Add "not":true only for an explicitly excluded term ("without asthma", "no history of stroke"). For an explicit sub-expression such as a parenthesised list use {"op":"or","terms":["insulin glargine","insulin detemir"]}.

A time window is never a search term. "in the last 2 years" is "last_months":24, "in the past 6 months" is "last_months":6, "within the last year" is "last_months":12. Put "last_months" on the rule the window describes - "diagnosed with cancer in the last 2 years" is {"term":"cancer","last_months":24}. Put it at the top level only when it applies to the whole query. Never emit a term like "diagnosed in last 2 years".

Omit every field you do not need. Never put age, sex or death in "rules" - "died of covid" is death "yes" plus {"term":"covid"}.

Query: "adults with cancer or diabetes"
{"age":[18,120],"sex":[],"death":"any","op":"or","rules":[{"term":"cancer"},{"term":"diabetes"}]}

Query: "women over 60 with type 2 diabetes on insulin (glargine or detemir)"
{"age":[60,120],"sex":["female"],"death":"any","op":"and","rules":[{"term":"type 2 diabetes"},{"op":"or","terms":["insulin glargine","insulin detemir"]}]}

Query: "teenagers who died of covid, without asthma"
{"age":[13,19],"sex":[],"death":"yes","op":"and","rules":[{"term":"covid"},{"term":"asthma","not":true}]}

Query: "females who were diagnosed with cancer in the last 2 years"
{"age":[0,120],"sex":["female"],"death":"any","op":"and","rules":[{"term":"cancer","last_months":24}]}
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
    return [_months_ago(months), None]


def _leaf(
    term: str, negated: bool = False, last_months: Any = None
) -> Dict[str, Any]:
    leaf: Dict[str, Any] = {
        "id": _node_id(),
        "exclude": bool(negated),
        "searchTerm": term.strip(),
        "rule": {"concept": None},
    }
    time_constraint = _time_constraint(last_months)
    if time_constraint:
        leaf["timeConstraint"] = time_constraint
    return leaf


def _operator(combinator: str) -> Dict[str, Any]:
    return {"id": _node_id(), "combinator": combinator, "exclude": False}


def _interleave(nodes: List[Dict[str, Any]], combinator: str) -> List[Dict[str, Any]]:
    interleaved: List[Dict[str, Any]] = []
    for node in nodes:
        if interleaved:
            interleaved.append(_operator(combinator))
        interleaved.append(node)
    return interleaved


def _group(
    terms: List[str], combinator: str, last_months: Any = None
) -> Dict[str, Any]:
    leaves = [_leaf(term) for term in terms if str(term).strip()]
    group: Dict[str, Any] = {
        "id": _node_id(),
        "exclude": False,
        "rules": _interleave(leaves, combinator),
    }
    time_constraint = _time_constraint(last_months)
    if time_constraint:
        group["timeConstraint"] = time_constraint
    return group


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


def _death(value: Any) -> Optional[Dict[str, Any]]:
    return DEATH_OPTIONS.get(str(value or "any").strip().lower())


def plan_to_tree(plan: Dict[str, Any]) -> Dict[str, Any]:
    nodes: List[Dict[str, Any]] = []
    for item in plan.get("rules") or []:
        terms = [t for t in (item.get("terms") or []) if str(t).strip()]
        if terms:
            nodes.append(
                _group(terms, item.get("op") or "and", item.get("last_months"))
            )
            continue
        term = str(item.get("term") or "").strip()
        if term:
            nodes.append(
                _leaf(term, item.get("not", False), item.get("last_months"))
            )

    return {
        "id": _node_id(),
        "rules": _interleave(nodes, plan.get("op") or "and"),
        "demographics": {
            "age": _age(plan.get("age")),
            "sex": _sex(plan.get("sex")),
            "race": [],
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
