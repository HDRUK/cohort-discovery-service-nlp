import time
from typing import List

import httpx

from logging_config import get_logger

log = get_logger()


class MedCATClient:
    def __init__(self, url: str, min_acc: float = 0.5, timeout: float = 5.0) -> None:
        self._url = url.rstrip("/")
        self._min_acc = min_acc
        self._timeout = timeout

    def expand(self, terms: List[str]) -> List[str]:
        """Call MedCAT to expand clinical terms into canonical pretty names."""
        t0 = time.monotonic()
        term_set = {t.lower() for t in terms}
        pretty_names = []
        for term in terms:
            try:
                resp = httpx.post(
                    f"{self._url}/api/process",
                    json={"content": {"text": term}},
                    timeout=self._timeout,
                )
                if resp.status_code != 200:
                    log.warning(
                        f"[MedCAT] term={term!r} — non-200 response: {resp.status_code}"
                    )
                    continue
                annotations = resp.json().get("result", {}).get("annotations", [])
                if not annotations:
                    log.info(f"[MedCAT] term={term!r} — no annotations returned")
                    continue
                for ann_group in annotations:
                    for ann in ann_group.values():
                        acc = ann.get("acc", 0)
                        status = ann.get("meta_anns", {}).get("Status", {}).get("value")
                        pretty_name = ann.get("pretty_name", "")
                        accepted = (
                            acc >= self._min_acc
                            and status == "Affirmed"
                            and pretty_name.lower() not in term_set
                        )
                        log.info(
                            f"[MedCAT] term={term!r} pretty_name={pretty_name!r}"
                            f" acc={acc:.3f} status={status} accepted={accepted}"
                        )
                        if accepted:
                            pretty_names.append(pretty_name)
            except Exception as e:
                log.warning(f"[MedCAT] term={term!r} — error: {e}")
        log.info(
            f"[MedCAT] {(time.monotonic() - t0) * 1000:.1f}ms"
            f" terms={len(terms)} expansions={len(pretty_names)}"
        )
        return pretty_names
