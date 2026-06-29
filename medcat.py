import os
from typing import List

import httpx


def get_medcat_names(terms: List[str]) -> List[str]:
    medcat_url = os.getenv("MEDCAT_URL", "").rstrip("/")
    if not medcat_url:
        print("[MedCAT] MEDCAT_URL is not set — skipping expansion")
        return []
    min_acc = float(os.getenv("MEDCAT_MIN_ACC", "0.5"))
    term_set = {t.lower() for t in terms}
    pretty_names = []
    for term in terms:
        try:
            resp = httpx.post(
                f"{medcat_url}/api/process",
                json={"content": {"text": term}},
                timeout=5.0,
            )
            if resp.status_code != 200:
                print(f"[MedCAT] term={term!r} — non-200 response: {resp.status_code}")
                continue
            annotations = resp.json().get("result", {}).get("annotations", [])
            if not annotations:
                print(f"[MedCAT] term={term!r} — no annotations returned")
                continue
            for ann_group in annotations:
                for ann in ann_group.values():
                    acc = ann.get("acc", 0)
                    status = ann.get("meta_anns", {}).get("Status", {}).get("value")
                    pretty_name = ann.get("pretty_name", "")
                    accepted = acc >= min_acc and status == "Affirmed" and pretty_name.lower() not in term_set
                    print(f"[MedCAT] term={term!r} pretty_name={pretty_name!r} acc={acc:.3f} status={status} accepted={accepted}")
                    if accepted:
                        pretty_names.append(pretty_name)
        except Exception as e:
            print(f"[MedCAT] term={term!r} — error: {e}")
    return pretty_names
