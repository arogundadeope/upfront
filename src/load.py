"""Load every parsed price file into DuckDB.

Tables:
  price_file(file_id, domain, path, sha256, hospital_name, last_updated, rows, loaded_at)
  price_row(... row shape from parse.py ...)
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import sys
import time
from datetime import datetime
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))
from parse import parse_file  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
HPT = ROOT / "data" / "raw" / "hpt"
PARSED = ROOT / "data" / "db" / "parsed"
DB = ROOT / "data" / "db" / "carecost.duckdb"

COLS = ["file_id", "hospital_name", "last_updated", "description", "code", "code_type", "codes", "billing_class", "setting",
        "modifiers", "gross", "cash", "min", "max", "notes", "payer_name", "plan_name", "negotiated_dollar", "negotiated_percentage",
        "negotiated_algorithm", "estimated_amount", "methodology", "median_amount", "p10", "p90", "count"]

DDL = """
create table if not exists price_file(file_id varchar primary key, domain varchar, path varchar, sha256 varchar, hospital_name varchar,
  last_updated varchar, n_rows bigint, loaded_at timestamp);
create table if not exists price_row(file_id varchar, hospital_name varchar, last_updated varchar, description varchar, code varchar,
  code_type varchar, codes varchar, billing_class varchar, setting varchar, modifiers varchar, gross double, cash double, min double,
  max double, notes varchar, payer_name varchar, plan_name varchar, negotiated_dollar double, negotiated_percentage double,
  negotiated_algorithm varchar, estimated_amount double, methodology varchar, median_amount double, p10 double, p90 double, count double);
"""


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_one(con, path: Path, force: bool = False) -> dict:
    domain = path.parent.name
    file_id = f"{domain}/{path.name}"
    digest = sha256(path)
    have = con.execute("select sha256, n_rows from price_file where file_id=?", [file_id]).fetchone()
    if have and have[0] == digest and not force:
        return {"file_id": file_id, "status": "unchanged", "rows": have[1]}
    PARSED.mkdir(parents=True, exist_ok=True)
    out = PARSED / (file_id.replace("/", "__") + ".csv.gz")
    t0 = time.time()
    n = 0
    hospital = updated = None
    with gzip.open(out, "wt", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore")
        w.writeheader()
        for row in parse_file(path, file_id):
            hospital = hospital or row.get("hospital_name")
            updated = updated or row.get("last_updated")
            w.writerow(row)
            n += 1
    con.execute("delete from price_row where file_id=?", [file_id])
    types = ["VARCHAR"]*10 + ["DOUBLE"]*4 + ["VARCHAR"]*3 + ["DOUBLE"]*2 + ["VARCHAR"] + ["DOUBLE"] + ["VARCHAR"] + ["DOUBLE"]*4
    struct = "{" + ", ".join("'%s': '%s'" % (c, t) for c, t in zip(COLS, types)) + "}"
    sql = ("insert into price_row select * from read_csv(?, header=true, auto_detect=false, delim=',', quote='\"', escape='\"', "
           "new_line='\\r\\n', max_line_size=10000000, nullstr='', columns=" + struct + ")")
    con.execute(sql, [str(out)])
    con.execute("insert or replace into price_file values (?,?,?,?,?,?,?,?)", [file_id, domain, str(path), digest, hospital, updated, n, datetime.now()])
    return {"file_id": file_id, "status": "loaded", "rows": n, "seconds": round(time.time() - t0, 1)}


def main(paths: list[str] | None = None, force: bool = False):
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB))
    con.execute(DDL)
    files = [Path(p) for p in paths] if paths else sorted(p for p in HPT.rglob("*") if p.is_file() and p.suffix.lower() in (".csv", ".json", ".txt") and p.stat().st_size > 1000)
    for p in files:
        try:
            print(load_one(con, p, force), flush=True)
        except Exception as e:  # noqa: BLE001
            print({"file_id": p.name, "status": f"error {type(e).__name__}: {str(e)[:200]}"}, flush=True)
    print(con.execute("select count(*) files, sum(n_rows) total_rows from price_file").fetchone())


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(args or None, force="--force" in sys.argv)
