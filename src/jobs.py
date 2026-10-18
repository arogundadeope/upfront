"""Background jobs for the API: interpret (Pine agent) + price, with a progress log the page polls."""
from __future__ import annotations

import threading
import time
import traceback
import uuid

JOBS: dict[str, dict] = {}
_lock = threading.Lock()


def start(fn, *args, **kwargs) -> str:
    job_id = uuid.uuid4().hex[:12]
    job = {"id": job_id, "status": "running", "events": [], "result": None, "error": None, "started": time.time(), "frame": None, "frame_at": None, "handle": None}
    with _lock:
        JOBS[job_id] = job

    def on_event(text):
        job["events"].append({"t": round(time.time() - job["started"], 1), "text": text})

    def on_frame(media_type, data):
        import base64
        if len(data) < 15000:      # a blank tab compresses to a few KB; not worth showing
            return
        job["frame"] = f"data:{media_type};base64," + base64.b64encode(data).decode()
        job["frame_at"] = round(time.time() - job["started"], 1)

    def runner():
        try:
            job["result"] = fn(*args, on_event=on_event, on_frame=on_frame, job=job, **kwargs)
            job["status"] = "done"
        except Exception as e:  # noqa: BLE001
            job["error"] = f"{type(e).__name__}: {e}"
            job["trace"] = traceback.format_exc()[-2000:]
            job["status"] = "failed"
        job["finished"] = time.time()

    threading.Thread(target=runner, daemon=True).start()
    return job_id


def get(job_id: str) -> dict | None:
    return JOBS.get(job_id)


def public(job: dict) -> dict:
    return {k: v for k, v in job.items() if k != "handle"}
