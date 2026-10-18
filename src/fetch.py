"""Find and download hospital price transparency files.

Local implementation: reads each hospital domain's cms-hpt.txt root file (required by CMS since
January 2025), parses its entries, and downloads the machine-readable files it points to.
The Pine implementation replaces `fetch_domain` with a browser session that can also fall back
to browsing when the root file is missing or its link is dead.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
ROOTS = RAW / "roots"
HPT = RAW / "hpt"
LOG = RAW / "fetch_log.csv"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) carecost/0.1"}


def read_root(domain: str, timeout: float = 20) -> tuple[str | None, list[dict]]:
    """Fetch https://<domain>/cms-hpt.txt. Returns (raw_text, entries)."""
    url = f"https://{domain}/cms-hpt.txt"
    try:
        r = httpx.get(url, headers=UA, timeout=timeout, follow_redirects=True)
    except Exception as e:  # noqa: BLE001
        return None, [{"error": str(e)}]
    if r.status_code != 200:
        return None, [{"error": f"HTTP {r.status_code}"}]
    data = r.content
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = data.decode("utf-16")
    else:
        text = data.decode("utf-8-sig", errors="replace")
    if "<html" in text[:500].lower():
        return None, [{"error": "HTML page, not a root file"}]
    ROOTS.mkdir(parents=True, exist_ok=True)
    (ROOTS / f"{domain}.txt").write_text(text, encoding="utf-8")
    return text, parse_root(text)


def parse_root(text: str) -> list[dict]:
    entries, cur = [], {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            if cur:
                entries.append(cur)
                cur = {}
            continue
        m = re.match(r"([a-zA-Z-]+)\s*:\s*(.*)", line)
        if not m:
            continue
        k, v = m.group(1).lower(), m.group(2).strip()
        if k == "location-name" and cur.get("location-name"):
            entries.append(cur)
            cur = {}
        cur[k] = v
    if cur:
        entries.append(cur)
    return [e for e in entries if e.get("mrf-url")]


def download(url: str, dest_dir: Path, timeout: float = 600) -> dict:
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = Path(urlparse(url).path).name or "file"
    if "." not in name:
        name = name + ".bin"
    dest = dest_dir / name
    t0 = time.time()
    try:
        with httpx.stream("GET", url, headers=UA, timeout=timeout, follow_redirects=True) as r:
            if r.status_code != 200:
                return {"url": url, "status": f"HTTP {r.status_code}"}
            cd = r.headers.get("content-disposition", "")
            m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', cd)
            if m:
                dest = dest_dir / Path(m.group(1)).name.replace(" (1)", "")
            if dest.exists() and dest.stat().st_size > 0:
                return {"url": url, "status": "exists", "path": str(dest), "bytes": dest.stat().st_size}
            h = hashlib.sha256()
            n = 0
            with open(dest, "wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
                    h.update(chunk)
                    n += len(chunk)
        return {"url": url, "status": "ok", "path": str(dest), "bytes": n, "sha256": h.hexdigest(), "seconds": round(time.time() - t0, 1)}
    except Exception as e:  # noqa: BLE001
        return {"url": url, "status": f"error {e}"}


def fetch_domain(domain: str, keep: list[str] | None = None) -> list[dict]:
    """Root file -> entries -> downloads. `keep` filters entries by location-name substring."""
    _, entries = read_root(domain)
    out = []
    if entries and "error" in entries[0]:
        return [{"domain": domain, **entries[0]}]
    seen = set()
    for e in entries:
        loc = e.get("location-name", "")
        if keep and not any(k.lower() in loc.lower() for k in keep):
            continue
        url = e["mrf-url"]
        if url in seen:
            continue
        seen.add(url)
        res = download(url, HPT / domain)
        res.update({"domain": domain, "location": loc})
        out.append(res)
        print(json.dumps(res), flush=True)
    return out


def log(rows: list[dict]) -> None:
    new = not LOG.exists()
    with open(LOG, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["domain", "location", "url", "status", "path", "bytes", "sha256", "seconds", "error"], extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    # usage: fetch.py domain [keep1,keep2,...]
    domain = sys.argv[1]
    keep = sys.argv[2].split(",") if len(sys.argv) > 2 else None
    log(fetch_domain(domain, keep))
