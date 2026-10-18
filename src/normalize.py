"""Map raw payer_name / plan_name labels from hospital files to (insurer, product, line_of_business).

Rule based. Unmatched labels are written to data/registry/payer_unmapped.csv for review.
line_of_business: commercial | medicare_advantage | medicare | medicaid | exchange | other
product: PPO | HMO | EPO | POS | ANY
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "db" / "carecost.duckdb"
REG = ROOT / "data" / "registry"

INSURERS = [
    ("Aetna", r"\baetna\b"),
    ("Anthem Blue Cross", r"\banthem\b|\bblue cross\b(?!.*blue shield)|\bbc\b(?!bs)|\bbcbs\b|\bwellpoint\b"),
    ("Blue Shield of California", r"\bblue shield\b|\bbsc\b|\bbsca\b"),
    ("Cigna", r"\bcigna\b|\bgreat[- ]west\b"),
    ("UnitedHealthcare", r"\bunited ?health|\buhc\b|\boptum\b|\bpacificare\b|\bsecure horizons\b|\bunited\b"),
    ("Aspire Health Plan", r"\baspire\b"),
    ("Jade Health", r"\bjade\b"),
    ("On Lok", r"\bon lok\b"),
    ("Central Health Plan", r"\bcentral health\b"),
    ("Bay Area Accountable Care", r"bay area accountable"),
    ("First Health / Coventry", r"first health|coventry"),
    ("MultiPlan / PHCS", r"multiplan|\bphcs\b"),
    ("Networks By Design", r"networks by design"),
    ("Interplan", r"interplan"),
    ("Stanislaus County Partners", r"stanislaus"),
    ("Progyny", r"progyny"),
    ("Sutter Health Plan", r"\bsutter health\b"),
    ("Health Net", r"\bhealth ?net\b|\bhealthnet\b"),
    ("Kaiser", r"\bkaiser\b"),
    ("Humana", r"\bhumana\b"),
    ("Sutter Health Plus", r"\bsutter health plus\b|\bshp\b"),
    ("Brown & Toland", r"brown.*toland|\bbtmg\b|\bbtp\b"),
    ("Hill Physicians", r"\bhill physicians?\b|\bhpmg\b"),
    ("Canopy Health", r"\bcanopy\b"),
    ("Western Health Advantage", r"western health advantage|\bwha\b"),
    ("Alignment", r"\balignment\b"),
    ("SCAN", r"\bscan\b"),
    ("Chinese Community Health Plan", r"\bcchp\b|chinese community|chinese hp"),
    ("San Francisco Health Plan", r"san francisco health plan|\bsfhp\b"),
    ("Alameda Alliance", r"alameda alliance"),
    ("Health Plan of San Mateo", r"san mateo|\bhpsm\b"),
    ("Santa Clara Family Health Plan", r"santa clara family|\bscfhp\b"),
    ("Partnership HealthPlan", r"partnership"),
    ("Valley Health Plan", r"valley health plan|\bvhp\b"),
    ("Contra Costa Health Plan", r"contra costa health|\bcchp\b"),
    ("Tricare", r"\btricare\b|\btriwest\b|\bchampus\b|\bhealth ?net federal\b"),
    ("Medicare", r"\bmedicare\b(?!.*(advantage|managed|adv|hmo|ppo|senior))|\bmcr\b(?!.*adv)|\btraditional medicare\b|\bcms\b"),
    ("Medi-Cal", r"\bmedi[- ]?cal\b|\bmedicaid\b"),
    ("Workers Comp", r"work(ers)?'?s? ?comp|\bwc\b"),
    ("Covered California", r"covered california|\bcovered ca\b|\bexchange\b"),
]

LOB_RULES = [
    ("medicare_advantage", r"medicare (advantage|managed|adv)|\bmcr ?adv|\bma\b|\bsenior|\bmedicare hmo|\bmedicare ppo|\badvantage\b|\b65 ?plus\b|secure horizons|\bscan\b|\balignment\b"),
    ("medicaid", r"medi[- ]?cal|medicaid|\bmcal\b|healthy kids|health worx|\bihss\b"),
    ("medicare", r"\bmedicare\b|\bmcr\b|\bcms\b"),
    ("exchange", r"covered california|covered ca|\bexchange\b|\bifp\b|\bindividual\b|\bepn\b|\bmarketplace\b"),
    ("other", r"work(ers)?'?s? ?comp|\bwc\b|tricare|triwest|champus|\bva\b|veterans|\bjail\b|\bcounty\b|student"),
]

PRODUCT_RULES = [("EPO", r"\bepo\b"), ("POS", r"\bpos\b"), ("PPO", r"\bppo\b"), ("HMO", r"\bhmo\b")]


def classify(payer: str | None, plan: str | None) -> dict:
    text = f"{payer or ''} | {plan or ''}".lower()
    insurer = None
    for name, pat in INSURERS:
        if re.search(pat, text):
            insurer = name
            break
    if insurer is None and payer:
        # unknown payer: keep its own name, cleaned, so nothing is silently dropped
        insurer = re.sub(r"\s+", " ", re.sub(r"[-_]|\ball other plans\b|\bcomm\b|\bcommercial\b", " ", payer, flags=re.I)).strip().title() or None
    if re.search(r"\bwcomp\b|\bwc\b|work(ers)?'?s? ?comp", text):
        insurer, lob_force = "Workers Comp", "other"
    elif re.search(r"behavioral|mental health|\bmhsa\b|substance", text):
        lob_force = "other"          # carve-out behavioral contracts are not medical rates
    else:
        lob_force = None
    lob = "commercial"
    for name, pat in LOB_RULES:
        if re.search(pat, text):
            lob = name
            break
    if insurer in ("Medicare",):
        lob = "medicare"
    if insurer in ("Medi-Cal",):
        lob = "medicaid"
    if insurer == "Workers Comp" or insurer == "Tricare":
        lob = "other"
    if lob_force:
        lob = lob_force
    products = [p for p, pat in PRODUCT_RULES if re.search(pat, text)]
    # "PPO,EPO" means the row applies to both; "ANY" means the label names no product
    product = ",".join(products) if products else "ANY"
    return {"insurer": insurer, "product": product, "line_of_business": lob}


def build(con) -> None:
    labels = con.execute("select payer_name, plan_name, count(*) n from price_row where payer_name is not null group by 1,2 order by 3 desc").fetchall()
    rows, unmapped = [], []
    for payer, plan, n in labels:
        c = classify(payer, plan)
        rows.append((payer, plan, c["insurer"], c["product"], c["line_of_business"], n))
        if c["insurer"] is None:
            unmapped.append((payer, plan, n))
    con.execute("create or replace table payer_map(payer_name varchar, plan_name varchar, insurer varchar, product varchar, line_of_business varchar, n bigint)")
    con.executemany("insert into payer_map values (?,?,?,?,?,?)", rows)
    REG.mkdir(parents=True, exist_ok=True)
    with open(REG / "payer_map.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["payer_name", "plan_name", "insurer", "product", "line_of_business", "rows"]); w.writerows(rows)
    with open(REG / "payer_unmapped.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["payer_name", "plan_name", "rows"]); w.writerows(unmapped)
    total = sum(r[5] for r in rows)
    covered = sum(r[5] for r in rows if r[2])
    print(f"labels {len(rows)}, mapped {len(rows)-len(unmapped)}, rows covered {covered/total:.1%}")
    for u in unmapped[:25]:
        print("  unmapped:", u)


if __name__ == "__main__":
    con = duckdb.connect(str(DB))
    build(con)
