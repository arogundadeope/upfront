"""Link each loaded price file to a CMS hospital (CCN) in the registry.

Automatic match on normalized names plus an override table for the ones that differ
(system files that cover several campuses, renamed hospitals, 'dba' names).
Writes table file_ccn(file_id, ccn, how) and data/registry/file_ccn.csv for review.
"""
from __future__ import annotations

import csv
import re
from difflib import SequenceMatcher
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "db" / "carecost.duckdb"
REG = ROOT / "data" / "registry"

# substring of file_id (lowercase) -> CCN
OVERRIDES = {
    "ucsf-medical-center": "050454",
    "van-ness": "050047",
    "davies": "050008",
    "mission-bernal": "050055",
    "chinese-hospital": "050407",
    "mills-peninsula": "050007",
    "alta-bates-summit-medical-center-summit": "050043",
    "alta-bates-summit-medical-center-alta-bates": "050305",
    "eden-medical": "050488",
    "novato": "050131",
    "sutter-delta": "050523",
    "sutter-solano": "050101",
    "sutter-santa-rosa": "050291",
    "stanford-health-care_": "050441",
    "tri-valley": "050283",
    "walnut-creek": "050180",
    "concord": "050496",
    "el-camino-hospital-mountain-view": "050308",
    "mountain-view": "050308",
    "washington": "050195",
    "good-samaritan": "050380",
    "marinhealth": "050360",
    "contra-costa-regional": "050276",
    "san-mateo-medical": "050113",
    "st-rose": "050002",
    "hayward-sisters": "050002",
    "hawthorne": "050043",
    "summit-campus": "050043",
    "herrick": "050305",
    "dignity-community-care": "050197",
    "northbay-medical": "050367",
    "sonoma-valley": "050090",
    "sequoia": "050197",
    "queen-of-the-valley": "050009",
    "santa-rosa-memorial": "050174",
    "petaluma": "050136",
    "st-helena": "050013",
    "seton": "050289",
    "oakland": "053301",
    "packard": "053305",
    "zuckerberg": "050228",
    "saint_francis": "050152",
    "st_marys": "050457",
}


def norm(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\b(hospital|medical|center|ctr|hosp|health|care|the|of|and|dba|campus|inc)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def link(con) -> list[tuple]:
    files = con.execute("select file_id, hospital_name from price_file").fetchall()
    hosp = con.execute("select ccn, name from hospital").fetchall()
    out = []
    for file_id, hname in files:
        fid = file_id.lower()
        ccn, how = None, None
        for key, c in OVERRIDES.items():
            if key in fid:
                ccn, how = c, f"override:{key}"
                break
        if ccn is None and hname:
            best = max(hosp, key=lambda h: SequenceMatcher(None, norm(h[1]), norm(hname)).ratio())
            score = SequenceMatcher(None, norm(best[1]), norm(hname)).ratio()
            if score >= 0.72:
                ccn, how = best[0], f"name:{score:.2f}"
        out.append((file_id, ccn, how or "unmatched"))
    # one file per hospital: keep the file with the most rows, mark the rest duplicate
    sizes = dict(con.execute("select file_id, n_rows from price_file").fetchall())
    best = {}
    for file_id, ccn, how in out:
        if ccn and (ccn not in best or sizes.get(file_id, 0) > sizes.get(best[ccn], 0)):
            best[ccn] = file_id
    out = [(f, c, h) if (c is None or best.get(c) == f) else (f, None, f"duplicate of {best[c]}") for f, c, h in out]
    con.execute("create or replace table file_ccn(file_id varchar, ccn varchar, how varchar)")
    con.executemany("insert into file_ccn values (?,?,?)", out)
    REG.mkdir(parents=True, exist_ok=True)
    with open(REG / "file_ccn.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["file_id", "ccn", "how"]); w.writerows(out)
    return out


if __name__ == "__main__":
    con = duckdb.connect(str(DB))
    for row in link(con):
        print(row)
