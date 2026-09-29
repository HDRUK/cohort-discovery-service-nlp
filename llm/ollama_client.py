import json
import time
from typing import Any, Dict, List, Optional

import httpx

from llm.query_plan import QUERY_PLAN_SCHEMA, SYSTEM_PROMPT
from logging_config import get_logger

log = get_logger()


class OllamaClient:
    def __init__(
        self,
        url: str,
        model: str,
        timeout: float = 120.0,
        keep_alive: str = "30m",
        num_ctx: int = 2048,
        num_predict: int = 512,
    ) -> None:
        self._url = url.rstrip("/")
        self._model = model
        self._timeout = timeout
        self._keep_alive = keep_alive
        self._num_ctx = num_ctx
        self._num_predict = num_predict

    @property
    def model(self) -> str:
        return self._model

    def _post(self, model: str, query: str, think: Optional[bool]) -> httpx.Response:
        body: Dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": query},
            ],
            "stream": False,
            "format": QUERY_PLAN_SCHEMA,
            "keep_alive": self._keep_alive,
            "options": {
                "temperature": 0,
                "num_ctx": self._num_ctx,
                "num_predict": self._num_predict,
            },
        }
        if think is not None:
            body["think"] = think
        return httpx.post(f"{self._url}/api/chat", json=body, timeout=self._timeout)

    def list_models(self) -> List[Dict[str, Any]]:
        response = httpx.get(f"{self._url}/api/tags", timeout=self._timeout)
        if response.status_code != 200:
            raise ValueError(
                f"Ollama returned HTTP {response.status_code}: {response.text.strip()}"
            )
        models = []
        for entry in response.json().get("models") or []:
            details = entry.get("details") or {}
            models.append(
                {
                    "name": entry.get("name"),
                    "size_gb": round((entry.get("size") or 0) / 1e9, 2),
                    "parameter_size": details.get("parameter_size"),
                    "quantization": details.get("quantization_level"),
                    "modified_at": entry.get("modified_at"),
                }
            )
        return sorted(models, key=lambda m: m["name"] or "")

    def loaded_models(self) -> List[Dict[str, Any]]:
        response = httpx.get(f"{self._url}/api/ps", timeout=self._timeout)
        if response.status_code != 200:
            return []
        return [
            {
                "name": entry.get("name"),
                "size_gb": round((entry.get("size") or 0) / 1e9, 2),
                "expires_at": entry.get("expires_at"),
            }
            for entry in response.json().get("models") or []
        ]

    def plan(self, query: str, model: Optional[str] = None) -> Dict[str, Any]:
        chosen = model or self._model
        t0 = time.monotonic()
        response = self._post(chosen, query, think=False)
        if response.status_code == 400 and "does not support thinking" in response.text:
            response = self._post(chosen, query, think=None)
        if response.status_code != 200:
            detail = response.text.strip()
            log.warning(f"[Ollama] model={chosen} HTTP {response.status_code}: {detail}")
            raise ValueError(
                f"Ollama returned HTTP {response.status_code}: {detail}. "
                f"If the model is missing, run: ollama pull {chosen}"
            )
        content = response.json().get("message", {}).get("content", "")
        elapsed = (time.monotonic() - t0) * 1000
        log.info(f"[Ollama] model={chosen} query={query!r} {elapsed:.1f}ms")
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            log.warning(f"[Ollama] model={chosen} returned non-JSON content: {content!r}")
            raise ValueError(f"Ollama returned content that is not valid JSON: {e}") from e
