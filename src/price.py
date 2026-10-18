"""Price an episode of care at every hospital near a location, for a given insurer, and rank the options.

Inputs (from the interpreter):
  components: list of dicts
     {"role": "facility"|"professional"|"anesthesia"|"lab", "code": "45378", "setting": "outpatient"|"inpatient",
      "label": "Hospital", "optional": False, "minutes": 30 (anesthesia only), "modifier": "26"|"TC"|None}
  insurer: normalized insurer name (see normalize.INSURERS) or None for cash
  product: "PPO"|"HMO"|"EPO"|"POS"|None
  location: (lat, lon); radius_miles
  benefits: {"deductible_left": 1000, "coinsurance": 0.2, "oop_left": 4000, "copay": None}

Rules:
  facility price: hospital file row for (code, setting, insurer, product) with a dollar amount; product match beats ANY;
     when several rows remain, take the lowest commercial rate and keep all candidates on the record.
     Percentage-of-charges rows use estimated_amount, else percentage * gross.
  professional and anesthesia: Medicare physician fee schedule at the hospital's locality times COMMERCIAL_MULTIPLE, labeled estimate.
  verified: the hospital file reports paid cases (count >= 11) for that payer row, or the same price appears in a second row.
  ranking: among priced and verified options with rating >= MIN_RATING (if rated): lowest total, then distance.
"""
from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "db" / "carecost.duckdb"
# the API reads a published copy so loads never block requests (see scripts/publish_db.sh)
if (ROOT / "data" / "db" / "serving.duckdb").exists():
    DB = ROOT / "data" / "db" / "serving.duckdb"

COMMERCIAL_MULTIPLE = 1.5     # commercial physician rates vs Medicare, Bay Area estimate
MIN_RATING = 3
VERIFIED_MIN_CASES = 11

# CMS physician fee localities for Bay Area counties
COUNTY_LOCALITY = {"SAN FRANCISCO": "05", "SAN MATEO": "05", "ALAMEDA": "05", "CONTRA COSTA": "05", "MARIN": "52",
                   "SANTA CLARA": "09", "SONOMA": "67", "NAPA": "51", "SOLANO": "53"}


def miles(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    a = 0.5 - math.cos((lat2 - lat1) * p) / 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * (1 - math.cos((lon2 - lon1) * p)) / 2
    return 3958.8 * 2 * math.asin(math.sqrt(a))


def connect(read_only=True):
    return duckdb.connect(str(DB), read_only=read_only)


def hospitals_near(con, lat, lon, radius, pediatric=False):
    rows = con.execute("""
        select h.ccn, coalesce(h.display_name, h.name), h.city, h.county, h.rating, h.lat, h.lon, f.file_id, f.hospital_name, f.last_updated, h.type
        from hospital h left join file_ccn m on m.ccn = h.ccn left join price_file f on f.file_id = m.file_id
        where h.lat is not null""").fetchall()
    out = []
    for r in rows:
        if (r[10] or "").lower().startswith("children") and not pediatric:
            continue   # children's hospitals only for pediatric requests
        d = miles(lat, lon, r[5], r[6])
        if d <= radius:
            out.append({"ccn": r[0], "name": r[1], "city": r[2], "county": r[3], "rating": r[4], "lat": r[5], "lon": r[6],
                        "file_id": r[7], "file_hospital": r[8], "file_date": r[9], "distance": round(d, 1), "kind": "Hospital"})
    return sorted(out, key=lambda x: x["distance"])


FACILITY_ROW = """ (r.code_type is null or r.code_type not in ('CDM','LOCAL','NDC'))
      and (r.billing_class is null or r.billing_class='facility')
      and (r.modifiers is null or r.modifiers not like '%26%')
      and (r.description is null or lower(r.description) not like 'pr %') """


def facility_rows(con, file_id, code, setting, insurer, product, gross_code=None):
    """Candidate hospital-file rows for one component at one hospital."""
    if insurer is None:
        rows = con.execute(f"""select distinct r.cash, r.gross, r.setting from price_row r where r.file_id=? and r.code=? and (r.setting in (?, 'both') or r.setting is null)
                              and {FACILITY_ROW} and r.cash > 0""", [file_id, code, setting]).fetchall()
        return [{"price": r[0], "source": "cash price", "gross": r[1], "setting": r[2], "cash": r[0], "payer": "cash", "plan": "", "verified": True, "estimate": False} for r in rows]
    q = """
        select r.payer_name, r.plan_name, m.product, r.negotiated_dollar, r.negotiated_percentage, r.negotiated_algorithm, r.estimated_amount,
               r.gross, r.cash, r.median_amount, r.count, r.methodology, r.setting
        from price_row r join payer_map m on m.payer_name = r.payer_name and coalesce(m.plan_name,'') = coalesce(r.plan_name,'')
        where r.file_id=? and r.code=? and (r.setting in (?, 'both') or r.setting is null)
          and """ + FACILITY_ROW + """
          and m.insurer=? and m.line_of_business='commercial'"""
    rows = con.execute(q, [file_id, code, setting, insurer]).fetchall()
    out = []
    gross_any = None
    for payer, plan, prod, dollar, pct, alg, est, gross, cash, med, cnt, meth, sett in rows:
        prods = (prod or "ANY").split(",")
        if product and prod != "ANY" and product not in prods:
            continue
        if pct is None and alg:
            pct = percent_in_text(alg)
        if pct is not None and not gross:
            if gross_any is None:
                gross_any = con.execute(f"select max(r.gross) from price_row r where r.file_id=? and r.code in (?, ?) and r.gross > 0 and {FACILITY_ROW}", [file_id, code, gross_code or code]).fetchone()[0] or 0
            gross = gross_any
        price, how = None, None
        if dollar is not None and dollar > 0:
            price, how = dollar, "negotiated dollar"
        elif est is not None and est > 0:
            price, how = est, "estimated from formula"
        elif pct is not None and gross:
            price, how = gross * pct / 100.0, f"{pct:g}% of charges"
        if price is None:
            continue
        out.append({"price": float(price), "source": how, "payer": payer, "plan": plan, "product": prod, "gross": gross, "cash": cash,
                    "median_paid": med, "cases": cnt, "methodology": meth, "setting": sett, "exact_product": bool(product) and product in prods,
                    "estimate": how != "negotiated dollar"})
    return out


PCT_PATTERNS = [r"all other op \(%bc\):\s*(\d{1,3}(?:\.\d+)?)", r"op(?:atient)?[^\[\]]*\(%bc\):\s*(\d{1,3}(?:\.\d+)?)",
                r"(\d{1,3}(?:\.\d+)?)\s*%\s*of\s*(?:billed\s*)?charges", r"percent(?:age)? of charges[^\d]{0,20}(\d{1,3}(?:\.\d+)?)"]


def percent_in_text(text: str):
    """A percent-of-charges term written in a hospital's algorithm text, if there is a clear one."""
    t = (text or "").lower()
    for pat in PCT_PATTERNS:
        m = re.search(pat, t)
        if m:
            v = float(m.group(1))
            if 5 <= v <= 100:
                return v
    return None


def codes_to_try(con, comp):
    """The component's code, its alternates, and the OPPS APC for each (some hospitals price outpatient lines by APC)."""
    base = [comp["code"]] + list(comp.get("alternates") or [])
    out = list(base)
    for code in base:
        row = con.execute("select apc from opps_rate where code=? and apc is not null and apc <> ''", [code]).fetchone()
        if row and row[0] and row[0] not in out:
            out.append(row[0])
    return out


def market_estimate(con, file_id, code, setting):
    """Median of other commercial insurers' dollar rates for this code at this hospital, plus cash. Labeled an estimate."""
    rows = con.execute("""
        select m.insurer, min(r.negotiated_dollar) from price_row r join payer_map m on m.payer_name=r.payer_name and coalesce(m.plan_name,'')=coalesce(r.plan_name,'')
        where r.file_id=? and r.code=? and (r.setting in (?, 'both') or r.setting is null) and """ + FACILITY_ROW + """
          and m.line_of_business='commercial' and r.negotiated_dollar > 0 group by 1""", [file_id, code, setting]).fetchall()
    cash = con.execute(f"""select min(r.cash) from price_row r where r.file_id=? and r.code=? and (r.setting in (?, 'both') or r.setting is null) and r.cash > 0 and {FACILITY_ROW}""", [file_id, code, setting]).fetchone()[0]
    prices = sorted(v for _, v in rows)
    if not prices and not cash:
        return None
    if prices:
        med = prices[len(prices) // 2]
        return {"price": float(med), "source": f"median of {len(prices)} other insurers", "payer": "other insurers", "plan": "", "cash": cash, "verified": False, "estimate": True, "cases": None}
    return {"price": float(cash), "source": "cash price", "payer": "cash", "plan": "", "cash": cash, "verified": False, "estimate": True, "cases": None}


def choose(cands):
    if not cands:
        return None
    exact = [c for c in cands if c.get("exact_product")]
    pool = exact or cands
    best = min(pool, key=lambda c: c["price"])
    counts = [c.get("cases") for c in pool if c.get("cases") is not None]
    cases = max(counts) if counts else None
    best = dict(best)
    # unverified only when the file reports zero paid cases for this payer, or the price is a formula estimate
    zero_cases = bool(counts) and cases == 0
    best["verified"] = (not zero_cases) and best["source"] in ("negotiated dollar", "cash price")
    best["cases"] = cases
    best["candidates"] = len(cands)
    return best


def pfs_amount(con, code, locality, facility=True, modifier=""):
    row = con.execute("select work_rvu, pe_fac, pe_nonfac, mp_rvu, cf from pfs_rvu where code=? and modifier=? limit 1", [code, modifier]).fetchone()
    g = con.execute("select work, pe, mp from gpci where locality=? limit 1", [locality]).fetchone()
    if not row or not g:
        return None
    work, pe_f, pe_nf, mp, cf = row
    pe = pe_f if facility else pe_nf
    return (work * g[0] + pe * g[1] + mp * g[2]) * cf


def anesthesia_amount(con, code, locality, minutes):
    b = con.execute("select base_units from anesthesia_base where code=?", [code]).fetchone()
    cf = con.execute("select cf from anesthesia_cf where locality=?", [locality]).fetchone()
    if not b or not cf:
        return None
    return (b[0] + minutes / 15.0) * cf[0]


def patient_share(total, benefits):
    if not benefits:
        return None
    if benefits.get("copay") is not None:
        return float(min(benefits["copay"], total))
    ded = max(0.0, float(benefits.get("deductible_left") or 0))
    coins = float(benefits.get("coinsurance") or 0)
    oop = benefits.get("oop_left")
    share = min(total, ded) + max(0.0, total - ded) * coins
    if oop is not None:
        share = min(share, float(oop))
    return round(share, 0)


def price_episode(components, insurer, product, location, radius=5.0, benefits=None, con=None):
    con = con or connect()
    lat, lon = location
    hospitals = hospitals_near(con, lat, lon, radius, pediatric=any(c.get('pediatric') for c in components))
    results = []
    for i, c in enumerate(components):
        c["_idx"] = i
    for h in hospitals:
        locality = COUNTY_LOCALITY.get((h["county"] or "").upper(), "05")
        lines, missing, verified = [], [], True
        for comp in components:
            role = comp["role"]
            if role == "facility":
                if not h["file_id"]:
                    missing.append(comp); continue
                setting = comp.get("setting", "outpatient")
                pick, used = None, None
                # the ideal code first, then the codes a hospital may use for the same care
                for code in codes_to_try(con, comp):
                    pick = choose(facility_rows(con, h["file_id"], code, setting, insurer, product, gross_code=comp["code"]))
                    if pick is not None:
                        used = code; break
                if pick is None:
                    # the hospital lists the code but publishes no rate for this insurer: estimate from other insurers' rates
                    for code in codes_to_try(con, comp):
                        est = market_estimate(con, h["file_id"], code, setting)
                        if est:
                            pick, used = est, code; break
                if pick is None:
                    listed = con.execute(f"select count(*) from price_row r where r.file_id=? and r.code in (select unnest(?::varchar[])) and {FACILITY_ROW}", [h["file_id"], [comp["code"]] + list(comp.get("alternates") or [])]).fetchone()[0]
                    h["why_missing"] = "not_listed" if not listed else ("no_insurer_rate" if insurer else "no_cash_price")
                    missing.append(comp); continue
                verified = verified and pick["verified"]
                note = None
                if used != comp["code"]:
                    note = f"billed as {used} here"
                if pick.get("estimate") and pick.get("payer") in ("other insurers", "cash"):
                    note = (note + "; " if note else "") + (f"no {insurer.split()[0]} rate published, estimated from other insurers" if insurer else "estimated")
                elif pick.get("estimate"):
                    note = (note + "; " if note else "") + f"{insurer.split()[0] if insurer else 'cash'} pays {pick['source']}"
                lines.append({**comp, "code_used": used, "amount": round(pick["price"]), "basis": pick["source"], "payer_row": f"{pick.get('payer','cash')} / {pick.get('plan','')}".strip(" /"),
                              "cash": pick.get("cash"), "gross": pick.get("gross"), "median_paid": pick.get("median_paid"), "cases": pick.get("cases"), "verified": pick["verified"],
                              "estimate": bool(pick.get("estimate")), "note": note})
            elif role == "professional":
                amt = pfs_amount(con, comp["code"], locality, facility=True, modifier=comp.get("modifier") or "")
                if amt is None:
                    missing.append(comp); continue
                lines.append({**comp, "amount": round(amt * COMMERCIAL_MULTIPLE), "basis": f"Medicare physician fee x {COMMERCIAL_MULTIPLE}", "estimate": True})
            elif role == "anesthesia":
                amt = anesthesia_amount(con, comp["code"], locality, comp.get("minutes") or 30)
                if amt is None:
                    missing.append(comp); continue
                lines.append({**comp, "amount": round(amt * COMMERCIAL_MULTIPLE), "basis": f"Medicare anesthesia units x {COMMERCIAL_MULTIPLE}", "estimate": True})
            else:
                amt = pfs_amount(con, comp["code"], locality, facility=False)
                if amt is None:
                    missing.append(comp); continue
                lines.append({**comp, "amount": round(amt * COMMERCIAL_MULTIPLE), "basis": f"Medicare fee x {COMMERCIAL_MULTIPLE}", "estimate": True})
        required = [l for l in lines if not l.get("optional")]
        fac_comps = [c for c in components if c["role"] == "facility" and not c.get("optional")]
        main = fac_comps[0] if fac_comps else None
        main_priced = bool(main) and any(l["role"] == "facility" and l is not None and l.get("_idx") == main.get("_idx") for l in required)
        missing_required = [m["label"] for m in missing if not m.get("optional")]
        total = sum(l["amount"] for l in required) if main_priced else None
        complete = bool(total) and not missing_required
        estimated = any(l.get("estimate") for l in required if l["role"] == "facility")
        zero_cases = any(l["role"] == "facility" and l.get("cases") == 0 for l in required)
        confidence = None if not total else ("partial" if not complete else ("estimate" if estimated else ("unconfirmed" if (zero_cases or not verified) else "verified")))
        results.append({**h, "lines": lines, "missing": missing_required, "why_missing": h.get("why_missing"), "total": total, "verified": bool(total and verified and not estimated),
                        "complete": complete, "confidence": confidence,
                        "you_pay": patient_share(total, benefits) if total else None,
                        "optional_total": sum(l["amount"] for l in lines if l.get("optional"))})
    priced = [r for r in results if r["total"]]
    tier = {"verified": 0, "estimate": 1, "unconfirmed": 1, "partial": 2}
    def rank_key(r):
        return (tier[r["confidence"]], 0 if (r["rating"] is None or r["rating"] >= MIN_RATING) else 1, r["total"], r["distance"])
    recommended = min(priced, key=rank_key) if priced else None
    return {"insurer": insurer, "product": product, "radius": radius, "hospitals": results, "recommended": recommended}


MAJOR_INSURERS = {"Aetna", "Anthem Blue Cross", "Blue Shield of California", "Cigna", "UnitedHealthcare", "Health Net", "Kaiser", "Humana", "Medicare"}


def other_plans(con, file_id, components, setting="outpatient", own_insurer=None):
    """What the hospital side of this episode costs under each insurer at one hospital, using the same fallbacks as pricing,
    plus Medicare where published and the cash price. Only insurers with every required facility line priced are listed."""
    fac = [c for c in components if c["role"] == "facility" and not c.get("optional")]
    if not fac:
        return []
    insurers = [r[0] for r in con.execute("""select distinct m.insurer from price_row r join payer_map m on m.payer_name=r.payer_name and coalesce(m.plan_name,'')=coalesce(r.plan_name,'')
        where r.file_id=? and m.insurer is not null and m.line_of_business in ('commercial','medicare')""", [file_id]).fetchall()]
    insurers = [i for i in insurers if i in MAJOR_INSURERS or i == own_insurer]
    out = []
    for ins in insurers:
        total, est = 0.0, False
        for c in fac:
            pick = None
            for code in codes_to_try(con, c):
                pick = choose(facility_rows(con, file_id, code, c.get("setting", setting), ins, None, gross_code=c["code"]))
                if pick:
                    break
            if not pick:
                # same fallback as pricing: other insurers' median or cash for a line this insurer has no term for
                for code in codes_to_try(con, c):
                    pick = market_estimate(con, file_id, code, c.get("setting", setting))
                    if pick:
                        break
            if not pick:
                total = None; break
            total += pick["price"]; est = est or bool(pick.get("estimate"))
        if total:
            out.append({"insurer": ins, "lob": "commercial", "price": round(total), "estimate": est})
    out.sort(key=lambda x: x["price"])
    cash = 0.0
    for c in fac:
        v = None
        for code in codes_to_try(con, c):
            v = con.execute(f"""select min(r.cash) from price_row r where r.file_id=? and r.code=? and (r.setting in (?, 'both') or r.setting is null)
                               and {FACILITY_ROW} and r.cash > 0""", [file_id, code, c.get("setting", setting)]).fetchone()[0]
            if v:
                break
        if not v:
            cash = None; break
        cash += v
    if cash:
        out.append({"insurer": "Cash", "lob": "cash", "price": round(cash)})
    return out


if __name__ == "__main__":
    comps = [
        {"role": "facility", "code": "45378", "setting": "outpatient", "label": "Hospital"},
        {"role": "professional", "code": "45378", "label": "Gastroenterologist"},
        {"role": "anesthesia", "code": "00812", "minutes": 30, "label": "Anesthesia"},
        {"role": "lab", "code": "88305", "label": "Lab, if a polyp is removed", "optional": True},
    ]
    insurer = sys.argv[1] if len(sys.argv) > 1 else "Blue Shield of California"
    product = sys.argv[2] if len(sys.argv) > 2 else "PPO"
    res = price_episode(comps, insurer, product, (37.7599, -122.4148), radius=5, benefits={"deductible_left": 1000, "coinsurance": 0.2, "oop_left": 4000})
    for h in res["hospitals"]:
        print(f'{h["name"][:40]:40s} {h["distance"]:>4} mi  rating {h["rating"]}  total {h["total"]}  you {h["you_pay"]}  verified {h["verified"]}  missing {h["missing"]}')
        for l in h["lines"]:
            print(f'     {l["label"]:28s} {l["amount"]:>7}  {l["basis"]}  {l.get("payer_row","")}')
    print("RECOMMENDED:", res["recommended"]["name"] if res["recommended"] else None)
