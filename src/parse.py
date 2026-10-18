"""Parse CMS hospital price transparency files (v2.x / v3.0, CSV tall, CSV wide, JSON) into one row shape.

Row shape (dict):
  file_id, hospital_name, last_updated, description, code, code_type, codes (all code|type pairs),
  billing_class, setting, modifiers, gross, cash, payer_name, plan_name,
  negotiated_dollar, negotiated_percentage, negotiated_algorithm, estimated_amount, methodology,
  median_amount, p10, p90, count, min, max, notes
"""
from __future__ import annotations

import csv
import io
import json
import re
import sys
from pathlib import Path
from typing import Iterator

import ijson

csv.field_size_limit(1 << 30)

NUM = re.compile(r"^-?\$?\s*[\d,]*\.?\d+$")


def num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("$", "").replace(",", "")
    if not s or s.upper() in ("N/A", "NA", "NULL", "NONE", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def open_text(path: Path):
    raw = open(path, "rb").read(4)
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return open(path, encoding="utf-16", newline="")
    return open(path, encoding="utf-8-sig", errors="replace", newline="")


# ---------------------------------------------------------------- CSV

def _find_header(rows: list[list[str]]) -> int:
    for i, r in enumerate(rows[:6]):
        low = [c.strip().lower() for c in r]
        if "description" in low and any(c.startswith("code|1") or c == "code" or c.startswith("code|") for c in low):
            return i
    raise ValueError("no column header row found")


def parse_csv(path: Path, file_id: str) -> Iterator[dict]:
    with open_text(path) as f:
        reader = csv.reader(f)
        head = []
        for _ in range(6):
            try:
                head.append(next(reader))
            except StopIteration:
                break
        hidx = _find_header(head)
        meta = {}
        if hidx >= 2:
            mk = [c.strip() for c in head[0]]
            mv = head[1]
            meta = {k.lower(): v for k, v in zip(mk, mv)}
        cols = [c.strip() for c in head[hidx]]
        low = [c.lower() for c in cols]
        idx = {c: i for i, c in enumerate(low)}
        pending = head[hidx + 1:]

        hospital = meta.get("hospital_name", path.stem)
        updated = meta.get("last_updated_on")

        def g(row, key):
            i = idx.get(key)
            return row[i].strip() if i is not None and i < len(row) and row[i] is not None else None

        code_cols = [(c, i) for i, c in enumerate(low) if re.match(r"code\|\d+$", c)]
        tall = "payer_name" in idx
        wide = [(c, i) for i, c in enumerate(low) if c.startswith("standard_charge|") and c.count("|") >= 3]
        # wide: standard_charge|<payer>|<plan>|negotiated_dollar etc.
        wide_groups: dict[tuple[str, str], dict[str, int]] = {}
        for c, i in wide:
            parts = cols[i].split("|")
            key = (parts[1], parts[2])
            wide_groups.setdefault(key, {})[parts[3].lower()] = i

        def codes_of(row):
            out = []
            for c, i in code_cols:
                code = row[i].strip() if i < len(row) else ""
                tcol = idx.get(c + "|type")
                ctype = row[tcol].strip() if tcol is not None and tcol < len(row) else ""
                if code:
                    out.append((code, ctype.upper()))
            return out

        def base(row):
            codes = codes_of(row)
            primary = pick_primary(codes)
            return {
                "file_id": file_id,
                "hospital_name": hospital,
                "last_updated": updated,
                "description": g(row, "description"),
                "code": primary[0] if primary else None,
                "code_type": primary[1] if primary else None,
                "codes": json.dumps(codes),
                "billing_class": g(row, "billing_class"),
                "setting": g(row, "setting"),
                "modifiers": g(row, "modifiers"),
                "gross": num(g(row, "standard_charge|gross")),
                "cash": num(g(row, "standard_charge|discounted_cash")),
                "min": num(g(row, "standard_charge|min")),
                "max": num(g(row, "standard_charge|max")),
                "notes": g(row, "additional_generic_notes"),
            }

        def emit(rows_iter):
            for row in rows_iter:
                if not row or all(not c.strip() for c in row):
                    continue
                b = base(row)
                if tall:
                    yield {
                        **b,
                        "payer_name": g(row, "payer_name"),
                        "plan_name": g(row, "plan_name"),
                        "negotiated_dollar": num(g(row, "standard_charge|negotiated_dollar")),
                        "negotiated_percentage": num(g(row, "standard_charge|negotiated_percentage")),
                        "negotiated_algorithm": g(row, "standard_charge|negotiated_algorithm"),
                        "estimated_amount": num(g(row, "estimated_amount")),
                        "methodology": g(row, "standard_charge|methodology"),
                        "median_amount": num(g(row, "median_amount")),
                        "p10": num(g(row, "10th_percentile")),
                        "p90": num(g(row, "90th_percentile")),
                        "count": num(g(row, "count")),
                    }
                elif wide_groups:
                    for (payer, plan), m in wide_groups.items():
                        def gi(k):
                            i = m.get(k)
                            return row[i].strip() if i is not None and i < len(row) else None
                        if not any(gi(k) for k in ("negotiated_dollar", "negotiated_percentage", "negotiated_algorithm")):
                            continue
                        yield {
                            **b,
                            "payer_name": payer,
                            "plan_name": plan,
                            "negotiated_dollar": num(gi("negotiated_dollar")),
                            "negotiated_percentage": num(gi("negotiated_percentage")),
                            "negotiated_algorithm": gi("negotiated_algorithm"),
                            "estimated_amount": num(gi("estimated_amount")),
                            "methodology": gi("methodology"),
                            "median_amount": None, "p10": None, "p90": None, "count": None,
                        }
                else:
                    yield {**b, "payer_name": None, "plan_name": None, "negotiated_dollar": None, "negotiated_percentage": None,
                           "negotiated_algorithm": None, "estimated_amount": None, "methodology": None,
                           "median_amount": None, "p10": None, "p90": None, "count": None}

        yield from emit(pending)
        yield from emit(reader)


# ---------------------------------------------------------------- JSON

def _open_json(path: Path):
    f = open(path, "rb")
    if f.read(3) != b"\xef\xbb\xbf":
        f.seek(0)
    return f


def parse_json(path: Path, file_id: str) -> Iterator[dict]:
    with _open_json(path) as f:
        # header fields come before standard_charge_information in the standard layout
        head = {}
        for prefix, event, value in ijson.parse(f):
            if prefix == "hospital_name":
                head["hospital_name"] = value
            elif prefix == "last_updated_on":
                head["last_updated"] = value
            elif prefix == "standard_charge_information" and event == "start_array":
                break
    hospital = head.get("hospital_name", path.stem)
    updated = head.get("last_updated")
    with _open_json(path) as f:
        for item in ijson.items(f, "standard_charge_information.item", use_float=True):
            codes = [(str(c.get("code", "")).strip(), str(c.get("type", "")).upper()) for c in item.get("code_information", []) if c.get("code")]
            primary = pick_primary(codes)
            for sc in item.get("standard_charges", []):
                b = {
                    "file_id": file_id,
                    "hospital_name": hospital,
                    "last_updated": updated,
                    "description": item.get("description"),
                    "code": primary[0] if primary else None,
                    "code_type": primary[1] if primary else None,
                    "codes": json.dumps(codes),
                    "billing_class": sc.get("billing_class"),
                    "setting": sc.get("setting"),
                    "modifiers": ",".join(sc.get("modifiers", [])) if isinstance(sc.get("modifiers"), list) else sc.get("modifiers"),
                    "gross": num(sc.get("gross_charge")),
                    "cash": num(sc.get("discounted_cash")),
                    "min": num(sc.get("minimum")),
                    "max": num(sc.get("maximum")),
                    "notes": sc.get("additional_generic_notes"),
                }
                payers = sc.get("payers_information") or []
                if not payers:
                    yield {**b, "payer_name": None, "plan_name": None, "negotiated_dollar": None, "negotiated_percentage": None,
                           "negotiated_algorithm": None, "estimated_amount": None, "methodology": None,
                           "median_amount": None, "p10": None, "p90": None, "count": None}
                for p in payers:
                    yield {
                        **b,
                        "payer_name": p.get("payer_name"),
                        "plan_name": p.get("plan_name"),
                        "negotiated_dollar": num(p.get("standard_charge_dollar")),
                        "negotiated_percentage": num(p.get("standard_charge_percentage")),
                        "negotiated_algorithm": p.get("standard_charge_algorithm"),
                        "estimated_amount": num(p.get("estimated_amount")),
                        "methodology": p.get("methodology"),
                        "median_amount": num(p.get("median_amount")),
                        "p10": num(p.get("10th_percentile")),
                        "p90": num(p.get("90th_percentile")),
                        "count": num(p.get("count")),
                    }


PRIORITY = ["CPT", "HCPCS", "MS-DRG", "DRG", "APR-DRG", "APC", "NDC", "RC", "CDM", "LOCAL"]


def pick_primary(codes: list[tuple[str, str]]):
    best = None
    for code, ctype in codes:
        t = ctype.upper()
        rank = PRIORITY.index(t) if t in PRIORITY else len(PRIORITY)
        if best is None or rank < best[0]:
            best = (rank, code, t)
    return (best[1], best[2]) if best else None


def parse_file(path: Path, file_id: str | None = None) -> Iterator[dict]:
    file_id = file_id or path.name
    if path.suffix.lower() == ".json":
        yield from parse_json(path, file_id)
    else:
        yield from parse_csv(path, file_id)


if __name__ == "__main__":
    p = Path(sys.argv[1])
    n = 0
    for row in parse_file(p):
        if n < 3:
            print(json.dumps(row)[:400])
        n += 1
    print("rows", n)
