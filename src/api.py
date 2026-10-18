"""HTTP API for the care cost app.

POST /api/interpret   {text, location:{lat,lon}, answers:{}}            -> episode (components, questions) via Pine's agent
POST /api/price       {components, insurer, product, location, radius, benefits} -> ranked hospitals
POST /api/answer      {text, location, answers, benefits}                -> interpret + price in one call (page JSON)
GET  /api/plans?ccn=&code=&setting=                                      -> same facility line under other insurers
GET  /api/insurers                                                       -> insurers present in the price table
GET  /                                                                   -> the page (web/index.html)
"""
from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jobs  # noqa: E402
import price as pricing  # noqa: E402
from interpret import interpret, validate  # noqa: E402
try:
    import pine  # noqa: E402
except Exception:  # noqa: BLE001
    pine = None

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
load_dotenv(ROOT / ".env")

app = FastAPI(title="carecost")


class Location(BaseModel):
    lat: float
    lon: float
    label: str | None = None


class InterpretIn(BaseModel):
    text: str
    location: Location | None = None
    answers: dict = {}


class PriceIn(BaseModel):
    components: list[dict]
    insurer: str | None = None
    product: str | None = None
    location: Location
    radius: float = 5.0
    benefits: dict | None = None


class AnswerIn(BaseModel):
    text: str
    location: Location
    answers: dict = {}
    benefits: dict | None = None
    radius: float = 5.0
    episode: dict | None = None   # the episode from the previous turn, so plan-type or insurer answers don't need the agent again


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


@app.get("/api/insurers")
def insurers():
    con = pricing.connect()
    rows = con.execute("select insurer, sum(n) n from payer_map where insurer is not null and line_of_business='commercial' group by 1 order by 2 desc").fetchall()
    return [{"insurer": r[0], "rows": r[1]} for r in rows]


@app.get("/api/codes")
def api_codes(q: str, limit: int = 20):
    """Search billing codes in the index by description words. Used by the Pine agent to confirm codes."""
    con = pricing.connect()
    words = [w for w in q.lower().split() if len(w) > 2][:5]
    if not words:
        return []
    cond = " and ".join(["lower(description) like ?" for _ in words])
    rows = con.execute(f"""
        select code, code_type, any_value(description) description, count(distinct file_id) hospitals,
               count(*) filter (where negotiated_dollar is not null) priced_rows
        from price_row where code is not null and code_type in ('HCPCS','CPT','MS-DRG') and {cond}
        group by 1,2 order by hospitals desc, priced_rows desc limit ?""", [f"%{w}%" for w in words] + [limit]).fetchall()
    return [{"code": r[0], "type": r[1], "description": r[2], "hospitals": r[3], "priced_rows": r[4]} for r in rows]


@app.post("/api/interpret")
def api_interpret(inp: InterpretIn):
    return interpret(inp.text, inp.answers, inp.location.model_dump() if inp.location else None)


@app.post("/api/price")
def api_price(inp: PriceIn):
    return pricing.price_episode(inp.components, inp.insurer, inp.product, (inp.location.lat, inp.location.lon), inp.radius, inp.benefits)


@app.get("/api/plans")
def api_plans(ccn: str, code: str, setting: str = "outpatient"):
    con = pricing.connect()
    row = con.execute("select file_id from file_ccn where ccn=?", [ccn]).fetchone()
    if not row:
        raise HTTPException(404, "no price file for that hospital")
    return pricing.other_plans(con, row[0], [{"role": "facility", "code": code, "setting": setting}])


def _price_episode(ep: dict, inp: AnswerIn) -> dict:
    insurer = ep.get("insurer")
    if insurer == "unknown":
        insurer = None
    res = pricing.price_episode(ep["components"], insurer, ep.get("product"), (inp.location.lat, inp.location.lon), inp.radius, inp.benefits)
    con = pricing.connect()
    plans = None
    if res["recommended"] and res["recommended"].get("file_id"):
        plans = pricing.other_plans(con, res["recommended"]["file_id"], ep["components"], own_insurer=insurer)
    return {"episode": ep, "pricing": res, "plans": plans}


def _answer_job(inp: AnswerIn, on_event=None, on_frame=None, job=None) -> dict:
    """Interpret with Pine's agent (stub if Pine is unavailable), then price."""
    on_event = on_event or (lambda s: None)
    handle = pine.Handle() if pine is not None else None
    if job is not None:
        job["handle"] = handle
    prev = inp.episode
    asked = [q.get("id") for q in (prev or {}).get("questions", [])]
    # reuse the agent's last episode when the only things answered since are plan type or insurer;
    # a variant answer can change the codes, so that goes back to the agent
    reusable = bool(prev and prev.get("components") and all(a in inp.answers for a in asked) and "variant" not in asked)
    if reusable:
        ep = dict(prev)
    elif pine is not None and pine.available():
        on_event("Asking Pine's agent what this procedure involves")
        try:
            ep = validate(pine.interpret_with_pine(inp.text, inp.answers, inp.location.model_dump(), on_event=on_event, on_frame=on_frame, handle=handle))
        except Exception as e:  # noqa: BLE001
            on_event(f"Pine agent failed ({type(e).__name__}); using fallback interpreter")
            ep = interpret(inp.text, inp.answers, inp.location.model_dump())
    else:
        ep = interpret(inp.text, inp.answers, inp.location.model_dump())
    # answers already given override what the agent returned
    if "product" in inp.answers and inp.answers["product"]:
        ep["product"] = inp.answers["product"]
    if "insurer" in inp.answers:
        ep["insurer"] = inp.answers["insurer"]
    ep["questions"] = [q for q in ep.get("questions", []) if q.get("id") not in inp.answers]
    if ep.get("insurer") is None and ("insurer" in inp.answers or ep.get("insurer") is None and "insurer" not in [q.get("id") for q in ep["questions"]]):
        ep["questions"] = [q for q in ep["questions"] if q.get("id") != "product"]   # cash: no plan type
        ep["product"] = None
    if not ep.get("components"):
        return {"episode": ep, "pricing": None, "plans": None}
    if ep.get("questions") or (ep.get("insurer") == "unknown"):
        if ep.get("insurer") == "unknown" and not any(q.get("id") == "insurer" for q in ep["questions"]):
            ep["questions"].append({"id": "insurer", "text": "Who is your insurance with?", "options": ["Aetna", "Anthem Blue Cross", "Blue Shield", "Cigna", "UnitedHealthcare", "Paying cash"]})
        return {"episode": ep, "pricing": None, "plans": None}
    on_event("Pricing every hospital near you")
    return _price_episode(ep, inp)


@app.post("/api/answer")
def api_answer(inp: AnswerIn):
    """Synchronous answer (no progress). Kept for scripts and tests."""
    return _answer_job(inp)


@app.post("/api/jobs/answer")
def api_jobs_answer(inp: AnswerIn):
    return {"job": jobs.start(_answer_job, inp)}


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str, frame: bool = True):
    j = jobs.get(job_id)
    if not j:
        raise HTTPException(404, "unknown job")
    out = jobs.public(j)
    if not frame:
        out["frame"] = None
    return out


class SteerIn(BaseModel):
    text: str


@app.post("/api/jobs/{job_id}/steer")
def api_job_steer(job_id: str, inp: SteerIn):
    j = jobs.get(job_id)
    if not j:
        raise HTTPException(404, "unknown job")
    h = j.get("handle")
    if not h or not h.turn_id:
        raise HTTPException(409, "nothing running to steer")
    try:
        h.steer(inp.text)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(409, f"could not steer: {type(e).__name__}: {str(e)[:200]}")
    j["events"].append({"t": 0, "text": "You: " + inp.text})
    return {"ok": True}


class FetchIn(BaseModel):
    domain: str


def _fetch_job(inp: FetchIn, on_event=None, on_frame=None, job=None) -> dict:
    from fetch import HPT, fetch_domain, log  # noqa: WPS433
    on_event = on_event or (lambda s: None)
    if pine is not None and pine.available():
        rows = pine.fetch_with_pine(inp.domain, HPT / inp.domain, on_event=on_event, on_frame=on_frame)
    else:
        rows = fetch_domain(inp.domain)
    log(rows)
    return {"files": rows}


@app.post("/api/jobs/fetch")
def api_jobs_fetch(inp: FetchIn):
    return {"job": jobs.start(_fetch_job, inp)}


class IndexIn(BaseModel):
    domain: str
    ccn: str | None = None


def _index_job(inp: IndexIn, on_event=None, on_frame=None, job=None) -> dict:
    """Fetch (Pine), parse, load, link and publish a hospital's file, so the next pricing call sees it."""
    import subprocess  # noqa: WPS433
    from fetch import HPT, fetch_domain, log  # noqa: WPS433
    from load import load_one  # noqa: WPS433
    import duckdb  # noqa: WPS433
    on_event = on_event or (lambda s: None)
    name = None
    if inp.ccn:
        row = pricing.connect().execute("select coalesce(display_name, name) from hospital where ccn=?", [inp.ccn]).fetchone()
        name = row[0] if row else None
    handle = pine.Handle() if pine is not None else None
    if job is not None:
        job["handle"] = handle
    if pine is not None and pine.available():
        rows = pine.fetch_with_pine(inp.domain, HPT / inp.domain, on_event=on_event, hospital=name, on_frame=on_frame, handle=handle)
    else:
        rows = fetch_domain(inp.domain)
    log(rows)
    ok = [r for r in rows if r.get("status") == "ok" and r.get("path")]
    if not ok:
        return {"files": rows, "loaded": [], "note": "no machine-readable file found"}
    on_event("Reading the file into the price index")
    con = duckdb.connect(str(ROOT / "data" / "db" / "carecost.duckdb"))
    loaded = []
    for r in ok:
        res = load_one(con, Path(r["path"]))
        loaded.append(res)
        on_event(f"{res.get('rows', 0):,} price rows loaded")
    if inp.ccn:
        for r in ok:
            fid = f"{inp.domain}/{Path(r['path']).name}"
            con.execute("delete from file_ccn where file_id=? or ccn=?", [fid, inp.ccn])
            con.execute("insert into file_ccn values (?,?,?)", [fid, inp.ccn, "live"])
    con.close()
    on_event("Mapping payer names")
    subprocess.run([str(ROOT / ".venv" / "bin" / "python"), str(ROOT / "src" / "normalize.py")], check=False, capture_output=True)
    if not inp.ccn:
        subprocess.run([str(ROOT / ".venv" / "bin" / "python"), str(ROOT / "src" / "link.py")], check=False, capture_output=True)
    subprocess.run(["sh", str(ROOT / "scripts" / "publish_db.sh")], check=False, capture_output=True)
    on_event("Index updated")
    return {"files": rows, "loaded": loaded}


@app.post("/api/jobs/index")
def api_jobs_index(inp: IndexIn):
    return {"job": jobs.start(_index_job, inp)}


@app.get("/api/hospital-domains")
def hospital_domains():
    """Best-guess websites for hospitals without a price file, for the live fetch button."""
    return DOMAINS


DOMAINS = {
    "050152": "saintfrancismemorial.org", "050457": "stmarysmedicalcenter.org", "050228": "zuckerbergsanfranciscogeneral.org",
    "050038": "scvmc.org", "050689": "sanramonmedctr.com", "050211": "alamedahealthsystem.org", "050320": "alamedahealthsystem.org",
    "050668": "lagunahonda.org", "050289": "ahmchealth.com", "050076": "healthy.kaiserpermanente.org",
}


@app.get("/api/pine/skills")
def api_pine_skills():
    """Skills on the warm Computer: Pine's own, the ones we learned, and pending drafts."""
    if pine is None or not pine.available():
        return {"available": False}
    c = pine.computer()
    return {"available": True, "computer": c.id, "skills": c.skills.list(), "drafts": c.skills.drafts()}


class RefineIn(BaseModel):
    skill: str
    version: int
    guidance: str


def _refine_job(inp: RefineIn, on_event=None, on_frame=None, job=None) -> dict:
    on_event = on_event or (lambda s: None)
    c = pine.computer()
    session = c.create_session(label="refine", browser=False)
    try:
        on_event(f"Refining skill {inp.skill} v{inp.version}")
        session.refine(inp.skill, inp.version, inp.guidance)
        drafts = c.skills.drafts()
        d = drafts[-1] if drafts else None
        if d and d.get("name") and d.get("version") is not None:
            c.skills.activate(d["name"], int(d["version"]))
            on_event(f"Activated {d['name']} v{d['version']}")
        return {"drafts": drafts, "skills": c.skills.list()}
    finally:
        try:
            c.destroy_session(session.name)
        except Exception:  # noqa: BLE001
            pass


@app.post("/api/pine/refine")
def api_pine_refine(inp: RefineIn):
    return {"job": jobs.start(_refine_job, inp)}


@app.on_event("shutdown")
def _shutdown():
    if pine is not None:
        try:
            pine.shutdown()
        except Exception:  # noqa: BLE001
            pass


app.mount("/static", StaticFiles(directory=str(WEB)), name="static")
