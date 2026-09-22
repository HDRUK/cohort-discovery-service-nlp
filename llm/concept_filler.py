from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

MAX_WORKERS = 5
UNMATCHED_CATEGORY = "Condition"


def _collect_leaves(node: Dict[str, Any], leaves: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if "rules" in node:
        for child in node.get("rules") or []:
            _collect_leaves(child, leaves)
    elif "rule" in node and node.get("searchTerm"):
        leaves.append(node)
    return leaves


def _concept_from_row(row: Dict[str, Any]) -> Dict[str, Any]:
    name = row.get("concept_name") or row.get("name")
    return {
        "concept_id": row.get("concept_id"),
        "name": name,
        "description": row.get("description") or name,
        "category": row.get("domain_id") or row.get("category"),
        "children": row.get("children") or [],
        "ncollections": row.get("ncollections") or 0,
        "all_synthetic": row.get("all_synthetic") or 0,
        "count": row.get("count"),
        "match_score": row.get("match_score") or 0,
        "collection_score": row.get("collection_score") or 0,
        "tokens": row.get("tokens") or [],
        "phrase_tokens": row.get("phrase_tokens") or [],
    }


def _unmatched_concept(term: str) -> Dict[str, Any]:
    return {
        "concept_id": None,
        "name": term,
        "description": term,
        "category": UNMATCHED_CATEGORY,
        "children": [],
    }


def fill_concepts(
    tree: Dict[str, Any],
    resolver: Any,
    threshold: float,
    phrase_first: bool = True,
    max_matches: Optional[int] = None,
    **resolve_kwargs: Any,
) -> Tuple[Dict[str, Any], List[str]]:
    leaves = _collect_leaves(tree, [])
    warnings: List[str] = []
    if not leaves:
        return tree, warnings

    def _run(leaf: Dict[str, Any]) -> Dict[str, Any]:
        return resolver.search(
            concept_names=[leaf["searchTerm"]],
            threshold=threshold,
            phrase_first=phrase_first,
            per_page=max_matches,
            **resolve_kwargs,
        )

    with ThreadPoolExecutor(max_workers=min(len(leaves), MAX_WORKERS)) as executor:
        results = list(executor.map(_run, leaves))

    for leaf, result in zip(leaves, results):
        term = leaf["searchTerm"]
        rows = (result or {}).get("data") or []
        if not rows:
            leaf["rule"]["concept"] = _unmatched_concept(term)
            warnings.append(
                f'We cannot find any matching concepts for your term "{term}".'
            )
            continue
        concept = _concept_from_row(rows[0])
        concept["alternatives"] = [_concept_from_row(row) for row in rows[1:]]
        leaf["rule"]["concept"] = concept

    tree["warnings"] = list(tree.get("warnings") or []) + warnings
    return tree, warnings
