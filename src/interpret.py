"""Turn a person's request into a priced episode definition.

Production path: Pine's agent gets a goal with the request, the location, the answers so far, and the
list of codes available in the price table, and returns JSON. Code validates every code against the table.

Until the Pine SDK is wired (see pine.py), a DEV stub answers one request shape so the page can be built.
The stub is not a product feature and is removed when pine.py is live.

Output shape:
{
  "procedure": "Diagnostic colonoscopy",
  "insurer": "Aetna", "product": "PPO",            # normalized, or None for cash
  "variant": "diagnostic",
  "components": [ {role, code, setting, label, optional, minutes, modifier} ... ],
  "questions": [ {"id": "variant", "text": "Screening or diagnostic?", "options": ["Screening", "Diagnostic"]} ],  # empty when ready to price
  "notes": ["A routine screening colonoscopy is free under preventive coverage."]
}
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "db" / "carecost.duckdb"
# the API reads a published copy so loads never block requests (see scripts/publish_db.sh)
if (ROOT / "data" / "db" / "serving.duckdb").exists():
    DB = ROOT / "data" / "db" / "serving.duckdb"

INSURER_WORDS = [
    ("Aetna", r"aetna"), ("Anthem Blue Cross", r"anthem|blue cross"), ("Blue Shield of California", r"blue shield|bsc"),
    ("Cigna", r"cigna"), ("UnitedHealthcare", r"united|uhc|optum"), ("Health Net", r"health ?net"), ("Kaiser", r"kaiser"),
]


def detect_insurer(text: str):
    t = text.lower()
    for name, pat in INSURER_WORDS:
        if re.search(pat, t):
            return name
    if re.search(r"\bcash\b|no insurance|uninsured|self[- ]pay", t):
        return None
    return "unknown"


def detect_product(text: str):
    t = text.lower()
    for p in ("PPO", "HMO", "EPO", "POS"):
        if re.search(rf"\b{p.lower()}\b", t):
            return p
    return None


def codes_exist(codes: list[str]) -> dict[str, bool]:
    con = duckdb.connect(str(DB), read_only=True)
    # a code counts only if some hospital attaches a payer term to it (a dollar, a formula or a percentage), not merely a list price
    rows = con.execute("""select distinct code from price_row where code in (select unnest(?::varchar[]))
                          and (negotiated_dollar > 0 or estimated_amount > 0 or negotiated_percentage > 0 or negotiated_algorithm is not null)""", [codes]).fetchall()
    have = {r[0] for r in rows}
    return {c: c in have for c in codes}


def validate(ep: dict) -> dict:
    """Drop facility components whose code is absent from every loaded file; keep a note."""
    fac_comps = [c for c in ep.get("components", []) if c["role"] == "facility"]
    codes = []
    for c in fac_comps:
        codes.append(c["code"]); codes.extend(c.get("alternates") or [])
    if fac_comps:
        ok = codes_exist(codes)
        if not any(ok.values()):
            # nothing a hospital near here bills under any of these codes: this is outside hospital pricing
            proc = ep.get("procedure") or "this"
            ep["components"] = []
            ep["questions"] = []
            ep["out_of_scope"] = True
            if not ep.get("notes"):
                ep["notes"] = [f"No hospital publishes prices for {proc.lower()}. We cover procedures done at hospitals and their outpatient departments."]
    return ep


# ------------------------------------------------------------------ DEV stub

def _stub(text: str, answers: dict, location) -> dict:
    t = text.lower()
    insurer = detect_insurer(text)
    product = answers.get("product") or detect_product(text)
    qs = []
    if insurer == "unknown":
        qs.append({"id": "insurer", "text": "Who is your insurance with?", "options": ["Aetna", "Anthem Blue Cross", "Blue Shield", "Cigna", "UnitedHealthcare", "Paying cash"]})
    if insurer not in (None, "unknown") and not product:
        qs.append({"id": "product", "text": f"Which {insurer.split()[0]} plan?", "options": ["PPO", "HMO"]})
    if "colonoscop" in t:
        variant = answers.get("variant")
        if not variant:
            qs.append({"id": "variant", "text": "Screening or diagnostic?", "options": ["Screening", "Diagnostic"]})
        ep = {
            "procedure": "Colonoscopy", "insurer": None if insurer == "unknown" else insurer, "product": product, "variant": variant,
            "components": [
                {"role": "facility", "code": "45378", "setting": "outpatient", "label": "Hospital"},
                {"role": "professional", "code": "45378", "label": "Gastroenterologist"},
                {"role": "anesthesia", "code": "00812", "minutes": 30, "label": "Anesthesia"},
                {"role": "lab", "code": "88305", "label": "Lab, if a polyp is removed", "optional": True},
            ],
            "questions": qs,
            "notes": ["A routine screening colonoscopy is free under preventive coverage."] if variant == "Screening" else [],
            "source": "dev-stub",
        }
        return validate(ep)
    return {"procedure": None, "insurer": insurer, "product": product, "components": [], "questions": qs,
            "notes": ["Interpreter not connected: Pine agent required for this request."], "source": "dev-stub"}


def interpret(text: str, answers: dict | None = None, location: dict | None = None) -> dict:
    answers = answers or {}
    if os.environ.get("PINE_API_KEY"):
        try:
            from pine import interpret_with_pine  # noqa: WPS433
            return validate(interpret_with_pine(text, answers, location))
        except ImportError:
            pass
    return _stub(text, answers, location)


if __name__ == "__main__":
    import sys
    print(json.dumps(interpret(sys.argv[1] if len(sys.argv) > 1 else "I need a colonoscopy, I have Aetna PPO", {"variant": "Diagnostic"}), indent=1))
