import os
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel

from llm.concept_filler import fill_concepts as resolve_tree_concepts
from llm.query_plan import interpretation_warnings, plan_to_tree
from logging_config import get_logger
from rules_engine import RuleEngine

log = get_logger()

router = APIRouter()

DEFAULT_THRESHOLD = int(os.getenv("DEFAULT_THRESHOLD", 90))
DEFAULT_MAX_MATCHES = 10

ENGINE = RuleEngine()


class LLMParseRequest(BaseModel):
    query: str
    fill_concepts: bool = True
    model: Optional[str] = None


def _select_resolver(request: Request) -> Any:
    state = request.app.state
    store = state.resolver_store
    if getattr(state, "backend", "sql") == "fuzzy" and store.fully_warm:
        return store.resolver
    return state.sql_resolver


@router.get("/llm/models")
def llm_models(request: Request) -> Dict[str, Any]:
    client = getattr(request.app.state, "ollama_client", None)
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Ollama is not configured. Set OLLAMA_URL to enable POST /llm/parse.",
        )

    try:
        available = client.list_models()
        loaded = client.loaded_models()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Ollama request failed: {e}",
        ) from e

    loaded_names = {m["name"] for m in loaded}
    for entry in available:
        entry["loaded"] = entry["name"] in loaded_names

    return {
        "default": client.model,
        "total": len(available),
        "models": available,
        "loaded": loaded,
    }


@router.post("/llm/parse")
def llm_parse(
    payload: LLMParseRequest,
    request: Request,
    threshold: float = Query(
        DEFAULT_THRESHOLD, description="Fuzzy match threshold 0-100"
    ),
    phrase_first: bool = Query(
        True, description="Prefer phrase overlap when token matching is available"
    ),
    max_matches: int = Query(
        DEFAULT_MAX_MATCHES,
        ge=1,
        description="Max concept matches per rule: the best match plus its alternatives",
    ),
    fill_concepts: Optional[bool] = Query(
        None,
        description="Resolve concepts for each rule. Overrides the body field when given; "
        "set false to return the plan and the blank tree without touching the database",
    ),
) -> Dict[str, Any]:
    client = getattr(request.app.state, "ollama_client", None)
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Ollama is not configured. Set OLLAMA_URL to enable POST /llm/parse.",
        )

    model = payload.model or client.model
    should_fill = payload.fill_concepts if fill_concepts is None else fill_concepts

    t0 = time.monotonic()
    try:
        plan = client.plan(payload.query, model=payload.model)
    except Exception as e:
        log.warning(f"[/llm/parse] query='{payload.query}' model={model} failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Ollama request failed: {e}",
        ) from e
    llm_ms = (time.monotonic() - t0) * 1000

    tree = plan_to_tree(plan)
    unsupported = ENGINE.warnings_for_features(
        ENGINE.find_unsupported_features(payload.query)
    )
    unsupported += interpretation_warnings(plan)

    resolve_ms = 0.0
    warnings: list = list(unsupported)
    if should_fill:
        t1 = time.monotonic()
        tree, resolve_warnings = resolve_tree_concepts(
            tree,
            _select_resolver(request),
            threshold,
            phrase_first=phrase_first,
            max_matches=max_matches,
        )
        warnings = warnings + resolve_warnings
        resolve_ms = (time.monotonic() - t1) * 1000

    tree["warnings"] = warnings

    log.info(
        f"[/llm/parse] query='{payload.query}' model={model} "
        f"llm={llm_ms:.1f}ms resolve={resolve_ms:.1f}ms "
        f"filled={should_fill} warnings={len(warnings)}"
    )

    return {
        "plan": plan,
        "tree": tree,
        "warnings": warnings,
        "model": model,
        "duration_ms": {"llm": round(llm_ms, 1), "resolve": round(resolve_ms, 1)},
    }
