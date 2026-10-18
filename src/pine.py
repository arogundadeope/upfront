"""Pine Computer adapter (pinesandbox Python SDK).

One Computer per server process, created lazily and kept warm; one session per job.
  interpret_with_pine(text, answers, location, on_event) -> episode dict (the agent decides procedure, components, codes, questions)
  fetch_with_pine(domain, dest_dir, on_event)            -> list of downloaded file records (the agent finds a hospital's price file)
on_event receives short progress strings for the UI.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

from pinesandbox import Client, TurnState  # noqa: E402

API_KEY = os.environ.get("PINE_API_KEY") or os.environ.get("PINE_SANDBOX_API_KEY", "")
ENDPOINT = os.environ.get("PINE_SANDBOX_ENDPOINT", "https://pinesandbox.io")
PUBLIC_API = os.environ.get("CARECOST_PUBLIC_URL", "")
IDLE = int(os.environ.get("PINE_IDLE_SECONDS", "1800"))

_lock = threading.Lock()
_client = None
_computer = None
_sessions = threading.BoundedSemaphore(3)   # the Computer allows three sessions at once
CRED_PATH = ROOT / "data" / "pine" / "credentials.json"
PERSIST = os.environ.get("PINE_PERSIST", "1") == "1"


def available() -> bool:
    return bool(API_KEY)


def _credentials():
    """Durable credentials for one persistent Computer, so learned skills and cookies survive restarts."""
    from pinesandbox import Credentials
    CRED_PATH.parent.mkdir(parents=True, exist_ok=True)
    if CRED_PATH.exists():
        return Credentials.parse(CRED_PATH.read_text()), False
    creds = Credentials.generate()
    CRED_PATH.write_text(creds.serialize())
    return creds, True


def computer():
    """The warm Computer for this process: persistent when PINE_PERSIST=1 (default), ephemeral otherwise."""
    global _client, _computer
    with _lock:
        if _computer is not None:
            try:
                if _computer.alive():
                    return _computer
            except Exception:  # noqa: BLE001
                pass
            _computer = None
        if _client is None:
            _client = Client(api_key=API_KEY, endpoint=ENDPOINT)
        if not PERSIST:
            _computer = _client.create_computer(ephemeral=True, idle_timeout_seconds=IDLE)
            return _computer
        creds, fresh = _credentials()
        try:
            _computer = _client.create_computer(credentials=creds, idle_timeout_seconds=IDLE) if fresh else _client.start_computer(creds, idle_timeout_seconds=IDLE)
        except Exception as e:  # noqa: BLE001
            # a runtime may still be attached from a previous process: stop it and start again
            try:
                _client.stop_computer(creds.id)
                time.sleep(2)
                _computer = _client.start_computer(creds, idle_timeout_seconds=IDLE)
            except Exception:  # noqa: BLE001
                raise e
        return _computer


def shutdown():
    global _computer
    with _lock:
        if _computer is not None:
            try:
                _computer.stop()
            finally:
                _computer = None


def _watch_events(session, turn_id, on_event, stop, on_frame=None):
    """Forward the agent's progress, activity titles and screen frames until stop is set."""
    last = None
    try:
        for ev in session.agent.events(skip_frames=on_frame is None):
            if stop.is_set():
                break
            fr = ev.frame()
            if fr is not None and on_frame and fr.data:
                on_frame(fr.media_type, fr.data)
                continue
            text = None
            prog = ev.progress()
            act = ev.activity() if prog is None else None
            if prog is not None:
                text = re.sub(r"[*`_#\[\]]", "", prog.text or "").strip()
                text = text.split("\n")[0]
            elif act is not None and act.title:
                title = act.title.strip()
                # raw shell commands are noise for a person; keep page, file and search steps
                if title.lower().startswith("run "):
                    low = title.lower()
                    title = "Checking billing codes in our price index" if "api/codes" in low else ("Fetching the price file" if "curl" in low else None)
                state = str(act.state or "").lower()
                text = (title + (" ✓" if ("complete" in state or "succeed" in state) else "")) if title else None
            if text and text != last:
                on_event(str(text)[:160])
                last = text
            if ev.result() is not None:
                break
    except Exception as e:  # noqa: BLE001
        on_event(f"(event stream ended: {type(e).__name__})")


class Handle:
    """What a job keeps about its live run, so the API can steer or cancel it."""
    def __init__(self):
        self.session = None
        self.turn_id = None

    def steer(self, text: str):
        if self.session and self.turn_id:
            return self.session.agent.steer(self.turn_id, text)

    def cancel(self):
        if self.session and self.turn_id:
            return self.session.agent.cancel(self.turn_id)


def run_goal(goal: str, on_event=None, label="job", timeout=420, answer_if_asked="Decide it yourself and continue; do not ask me.",
             on_frame=None, handle: Handle | None = None, skills=None) -> tuple:
    """Run one agent turn in a fresh session. Returns (session, turn). Holds one of the Computer's three session slots."""
    on_event = on_event or (lambda s: None)
    _sessions.acquire()
    comp = computer()
    session = comp.create_session(label=f"{label}-{uuid.uuid4().hex[:6]}", browser=True)
    on_event("Pine Computer ready")
    turn = session.agent.run(goal, idempotency_key=f"{label}-{uuid.uuid4().hex}", max_wall_seconds=timeout, skills=skills or None)
    if handle is not None:
        handle.session, handle.turn_id = session, turn.turn_id
    stop = threading.Event()
    t = threading.Thread(target=_watch_events, args=(session, turn.turn_id, on_event, stop, on_frame), daemon=True)
    t.start()
    t0 = time.time()
    try:
        while time.time() - t0 < timeout + 30:
            turn = session.agent.wait(turn.turn_id, timeout=30)
            if turn.state in (TurnState.RUNNING, TurnState.CANCELING):
                continue
            if turn.state == TurnState.NEEDS_INPUT:
                on_event(f"Agent asked: {turn.question}")
                turn = session.agent.answer(turn.turn_id, answer_if_asked)
                continue
            break
    finally:
        stop.set()
    return session, turn


def release(session):
    """Close a job's session and free its slot."""
    try:
        computer().destroy_session(session.name)
    except Exception:  # noqa: BLE001
        pass
    try:
        _sessions.release()
    except ValueError:
        pass


def learn_skill(session, on_event=None) -> dict | None:
    """Draft a skill from the session's last finished run and activate it. Takes minutes; call from a background thread."""
    on_event = on_event or (lambda s: None)
    try:
        on_event("Pine is writing down what it learned as a skill")
        result = session.learn(scaffold=LEARN_SCAFFOLD)
        drafts = computer().skills.drafts()
        if not drafts:
            return None
        d = drafts[-1]
        name, version = d.get("name"), d.get("version")
        if name and version is not None:
            computer().skills.activate(name, int(version))
            on_event(f"Skill '{name}' v{version} activated for future runs")
            return {"name": name, "version": version, "body": d.get("body") or d.get("skill_md")}
    except Exception as e:  # noqa: BLE001
        on_event(f"(skill learning skipped: {type(e).__name__})")
    return None


def active_skill_names(prefix: str | None = None) -> list[str]:
    try:
        return [k["name"] for k in computer().skills.list() if k.get("origin") != "pine" and (not prefix or k["name"].startswith(prefix))]
    except Exception:  # noqa: BLE001
        return []


def _read_json(session, name: str):
    try:
        return json.loads(session.read_file(name))
    except Exception:  # noqa: BLE001
        pass
    for a in session.list_artifacts():
        if a.filename.endswith(name):
            return json.loads(session.read_artifact(a.id))
    return None


# ---------------------------------------------------------------- goals

INTERPRET_GOAL = """You are helping a person find out where to get a medical procedure done near them and what it will cost.

The person wrote: "{text}"
Answers they have already given: {answers}
Their location: {location}

Work out the episode of care this person actually needs, as a hospital and its doctors would bill it:
- the hospital (facility) side: one or more CPT/HCPCS lines (or one MS-DRG for an inpatient stay), each with setting "outpatient" or "inpatient".
  For a scheduled procedure this is usually one line (the procedure code). For an injury or illness that is treated in the emergency department
  (a broken bone, a cut, chest pain) the hospital bills an ED visit level (99283 or 99284), the imaging (for example 73090 forearm X-ray), and the
  treatment done there (a reduction, a splint 29125 or cast 29075, sutures), so list those lines.
  Hospitals package the same care differently, so give each facility line "alternates": other codes a hospital may use for the same care, in order of preference
  (for example 25605 with alternates ["25600", "29125"]).
- the physician professional service (the surgeon, radiologist, ED physician or orthopedist; usually the main CPT code);
- anesthesia if the procedure normally has it: the 00xxx or 01xxx anesthesia code and typical minutes;
- any usual add-on that is only sometimes billed (for example pathology 88305 after a biopsy), marked optional.
You can confirm which codes hospitals actually list by fetching {api}/api/codes?q=<words> (JSON). Prefer codes many hospitals list.
Use your own medical knowledge for the mapping; the code search is only to check availability.
Identify the insurer and plan type (PPO, HMO, EPO) if the person said, or cash if they have no insurance.
Ask a question only when the answer changes the price or coverage: a variant that changes the code (screening vs diagnostic, with or without contrast,
arthroscopy vs replacement, laparoscopic vs open), the plan type when it is missing, or the insurer if it is missing. Never ask anything else, and never ask more than three questions in total across turns.

Write the result as JSON only, to a file named episode.json in the working directory, with exactly this shape:
{{
 "procedure": "<plain name a person would recognize>",
 "insurer": "<Aetna | Anthem Blue Cross | Blue Shield of California | Cigna | UnitedHealthcare | Health Net | Kaiser | null for cash | \\"unknown\\" if not stated>",
 "product": "<PPO | HMO | EPO | POS | null>",
 "variant": "<short label or null>",
 "components": [
   {{"role": "facility", "code": "45378", "setting": "outpatient", "label": "Hospital", "alternates": []}},
   {{"role": "professional", "code": "45378", "label": "Gastroenterologist"}},
   {{"role": "anesthesia", "code": "00812", "minutes": 30, "label": "Anesthesia"}},
   {{"role": "lab", "code": "88305", "label": "Lab, if a polyp is removed", "optional": true}}
 ],
 "questions": [ {{"id": "variant", "text": "Screening or diagnostic?", "options": ["Screening", "Diagnostic"]}} ],
 "notes": ["one short sentence the person should know, if any"]
}}
Scope: we price care delivered and billed by hospitals and their outpatient departments (surgery, imaging, endoscopy, emergency and inpatient care, infusions, labs done at a hospital).
If the request is for care hospitals do not provide or price (routine dental work, eyeglasses or contact lenses, cosmetic procedures, veterinary care, over-the-counter items, or a service you cannot map to any hospital billing code), do not ask questions:
return "components": [] and put one plain sentence in "notes" saying what kind of provider does this and that they do not publish price files, for example "Dental bonding is done by dentists, and dental offices do not publish price files; we cover hospital and outpatient procedures."
Give every facility line its own plain label naming the service a person would see on the bill (for example "Emergency visit", "X-ray", "Fracture set and cast", "Hospital" only when there is a single procedure line). The first facility line must be the main service of the episode.
Rules: "questions" is an empty list when nothing more is needed. Use question ids "variant", "product", "insurer" only. When "insurer" is unknown, add the insurer question with options ["Aetna","Anthem Blue Cross","Blue Shield","Cigna","UnitedHealthcare","Paying cash"]. Labels name the party that bills (Hospital, Surgeon, Radiologist, Anesthesia, Lab). Finish as soon as the file is written. Do not ask me anything.
"""

LEARN_SCAFFOLD = """Write this as a general, reusable skill named find-hospital-price-file for locating and downloading ANY hospital's CMS
price transparency machine-readable file given its website domain and hospital name, not just this hospital. Steps: 1) open https://<domain>/cms-hpt.txt
and parse location-name / mrf-url pairs; 2) if missing or dead, search the site for the price transparency / standard charges page and the
machine-readable (CSV or JSON) link, trying the parent health system's site when the hospital was acquired; 3) take only the file for the named hospital
when a system file lists many; 4) download with curl -L into the working directory; 5) verify size and format; 6) write manifest.json with
location, source_page_url, mrf_url, filename, bytes. Note common hosts (Box shared links, mrfs.hyvehealthcare.com, system CDNs) and that some
sites block plain curl but work in the browser."""

FETCH_GOAL = """Find the machine-readable hospital price transparency file for {hospital} (website https://{domain}).
First open https://{domain}/cms-hpt.txt, which lists each location and its mrf-url. If that is missing or its link is dead,
browse the site for the price transparency or standard charges page and find the download link for the machine-readable file
(CSV or JSON, usually with "standardcharges" in the name; not the shoppable services PDF).
If a root file or page lists many locations (a health system), take only the file(s) for {hospital}. Download them into the working directory using the shell (curl -L -o <name> <url>), then write
manifest.json in the working directory: a JSON list with, for each file, "location", "source_page_url", "mrf_url", "filename", "bytes".
Do not ask me anything.
"""


def interpret_with_pine(text: str, answers: dict, location: dict | None, on_event=None, on_frame=None, handle=None) -> dict:
    on_event = on_event or (lambda s: None)
    goal = INTERPRET_GOAL.format(text=text, answers=json.dumps(answers), location=json.dumps(location or {}), api=PUBLIC_API or "http://localhost:8010")
    session, turn = run_goal(goal, on_event, label="interpret", timeout=300, on_frame=on_frame, handle=handle)
    try:
        ep = _read_json(session, "episode.json")
        if ep is None:
            # last resort: a JSON object in the summary
            m = re.search(r"\{.*\}", (turn.result.summary if turn.result else "") or "", re.S)
            ep = json.loads(m.group(0)) if m else None
        if ep is None:
            raise RuntimeError(f"agent finished ({turn.state}) without episode.json")
        ep.setdefault("questions", [])
        ep.setdefault("notes", [])
        ep["source"] = "pine"
        ep["summary"] = turn.result.summary if turn.result else None
        return ep
    finally:
        release(session)


def fetch_with_pine(domain: str, dest_dir: Path, on_event=None, hospital: str | None = None, on_frame=None, handle=None, learn=True) -> list[dict]:
    on_event = on_event or (lambda s: None)
    skills = active_skill_names()
    if skills:
        on_event(f"Using learned skill: {skills[-1]}")
    session, turn = run_goal(FETCH_GOAL.format(domain=domain, hospital=hospital or "the hospital"), on_event, label="fetch", timeout=420, on_frame=on_frame, handle=handle, skills=skills[-1:] or None)
    out = []
    try:
        manifest = _read_json(session, "manifest.json") or []
        dest_dir.mkdir(parents=True, exist_ok=True)
        for m in manifest:
            name = m.get("filename")
            if not name:
                continue
            on_event(f"Downloading {name} from the Computer")
            data = session.read_file(name)
            p = dest_dir / Path(name).name
            p.write_bytes(data)
            out.append({"domain": domain, "location": m.get("location"), "url": m.get("mrf_url"), "path": str(p), "bytes": len(data), "status": "ok"})
        if out and learn and PERSIST and not skills:
            # first successful fetch: let the Computer learn the workflow in the background, then free the session
            def _learn():
                try:
                    learn_skill(session, on_event)
                finally:
                    release(session)
            threading.Thread(target=_learn, daemon=True).start()
            session = None
        return out
    finally:
        if session is not None:
            release(session)


if __name__ == "__main__":
    import sys
    t0 = time.time()
    ep = interpret_with_pine(sys.argv[1] if len(sys.argv) > 1 else "I need a colonoscopy, I have Aetna PPO", {}, {"lat": 37.7599, "lon": -122.4148, "city": "San Francisco"}, on_event=lambda s: print("  ·", s, flush=True))
    print(json.dumps(ep, indent=1))
    print("seconds", round(time.time() - t0, 1))
    shutdown()
