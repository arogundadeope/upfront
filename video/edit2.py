"""Shot-based cut: zooms, highlights, one caption per shot synced to the voice, music bed ducked under narration.

usage: edit2.py TAKE_NAME MUSIC_FILE [out.mp4]
Needs: video/take_<name>/{frames, frames.jsonl, markers.json, rects.json}, video/v2_*.mp3
"""
import json, os, subprocess, sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter

HERE = Path(__file__).resolve().parent
TAKE = HERE / f"take_{sys.argv[1]}"
MUSIC = sys.argv[2]
OUT = Path(sys.argv[3]) if len(sys.argv) > 3 else HERE / "upfront_demo.mp4"
FPS = 30
W, H = 1920, 1080
NAVY = (10, 22, 40)
TEAL = (0, 168, 140)
WHITE = (255, 255, 255)
MUTED = (160, 170, 185)

def clip_seconds(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout.strip()
    return float(out) if out else 0.0

SCRIPT = {p["id"]: p["text"] for p in json.load(open(HERE / "script2.json"))}
VO_LEN = {k: clip_seconds(HERE / f"v2_{k}.mp3") for k in SCRIPT}

frames = [json.loads(l) for l in open(TAKE / "frames.jsonl")]
m = json.load(open(TAKE / "markers.json"))
R = json.load(open(TAKE / "rects.json")) if (TAKE / "rects.json").exists() else {}
print("frames", len(frames), "markers", list(m))


def font(size, bold=False):
    try:
        return ImageFont.truetype("/System/Library/Fonts/SFNS.ttf", size)
    except Exception:
        return ImageFont.load_default()


def frame_at(t):
    lo, hi = 0, len(frames) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if frames[mid]["ts"] <= t:
            lo = mid
        else:
            hi = mid - 1
    return TAKE / "frames" / frames[lo]["file"]


def rect(marker, key):
    r = R.get(marker, {}).get(key)
    return tuple(r) if r else None


def rows(marker):
    return R.get(marker, {}).get("rows") or []


def money(s):
    import re
    mm = re.search(r"\$[\d,]+", s or "")
    return mm.group(0) if mm else None


# ---------------------------------------------------------------- drawing
def load(path):
    im = Image.open(path).convert("RGB")
    return im if im.size == (W, H) else im.resize((W, H))


def zoom_to(im, target, factor):
    """Crop around target rect (viewport px) by factor and resize back to full frame."""
    if not target or factor <= 1.0:
        return im, None
    cx = target[0] + target[2] / 2
    cy = target[1] + target[3] / 2
    cw, ch = W / factor, H / factor
    x0 = min(max(0, cx - cw / 2), W - cw)
    y0 = min(max(0, cy - ch / 2), H - ch)
    return im.crop((int(x0), int(y0), int(x0 + cw), int(y0 + ch))).resize((W, H), Image.LANCZOS), (x0, y0, factor)


def map_rect(r, view):
    """Rect in viewport px -> rect in zoomed frame px."""
    if not view:
        return r
    x0, y0, f = view
    return ((r[0] - x0) * f, (r[1] - y0) * f, r[2] * f, r[3] * f)


def highlight(im, r, pad=14, dim=0.34):
    if not r:
        return im
    x, y, w, h = r
    box = (x - pad, y - pad, x + w + pad, y + h + pad)
    overlay = Image.new("RGBA", (W, H), (10, 22, 40, int(255 * dim)))
    od = ImageDraw.Draw(overlay)
    od.rounded_rectangle(box, radius=18, fill=(0, 0, 0, 0))
    base = im.convert("RGBA")
    base = Image.alpha_composite(base, overlay)
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.rounded_rectangle((box[0] - 6, box[1] - 6, box[2] + 6, box[3] + 6), radius=24, outline=TEAL + (150,), width=12)
    glow = glow.filter(ImageFilter.GaussianBlur(10))
    base = Image.alpha_composite(base, glow)
    d = ImageDraw.Draw(base)
    d.rounded_rectangle(box, radius=18, outline=TEAL + (255,), width=4)
    return base.convert("RGB")


def caption(im, text, progress=1.0):
    if not text:
        return im
    f = font(50, True)
    d = ImageDraw.Draw(im)
    full_w = d.textlength(text, font=f)
    shown = words_visible(text, progress)
    if not shown:
        return im
    w = d.textlength(shown, font=f)
    pad = 34
    x, y = (W - full_w) / 2, H - 168     # left edge fixed, pill grows to the right as words appear
    box = (int(x - pad), int(y - 20), int(x + w + pad), int(y + 78))
    # frosted glass: blur what's under the pill, darken, thin light edge
    region = im.crop(box).filter(ImageFilter.GaussianBlur(14))
    region = Image.blend(region, Image.new("RGB", region.size, NAVY), 0.62)
    mask = Image.new("L", region.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, region.size[0] - 1, region.size[1] - 1), radius=26, fill=255)
    im.paste(region, box[:2], mask)
    d = ImageDraw.Draw(im, "RGBA")
    d.rounded_rectangle(box, radius=26, outline=(255, 255, 255, 60), width=1)
    d.text((x, y), shown, font=f, fill=WHITE)
    return im


_AURORA = None
def aurora():
    """The page's background: navy with a soft teal glow top-left and a blue glow top-right."""
    global _AURORA
    if _AURORA is None:
        small = Image.new("RGB", (W // 8, H // 8), NAVY)
        d = ImageDraw.Draw(small)
        d.ellipse((-60, -70, 110, 70), fill=(0, 92, 78))
        d.ellipse((150, -80, 300, 40), fill=(40, 70, 130))
        small = small.filter(ImageFilter.GaussianBlur(26))
        _AURORA = Image.blend(Image.new("RGB", (W, H), NAVY), small.resize((W, H), Image.BILINEAR), 0.55)
    return _AURORA.copy()


def words_visible(text, progress):
    ws = text.split()
    k = int(round(min(1.0, max(0.0, progress)) * len(ws) + 0.0001))
    return " ".join(ws[:k])


def draw_center_lines(d, lines, y, f, fill=WHITE, gap=96, progress=1.0):
    """Draw centered lines, revealing words left to right across all lines by progress."""
    total = sum(len(l.split()) for l in lines)
    shown = int(round(progress * total + 0.0001))
    for line in lines:
        ws = line.split()
        take = max(0, min(len(ws), shown))
        shown -= take
        full_w = d.textlength(line, font=f)
        x = (W - full_w) / 2
        d.text((x, y), " ".join(ws[:take]), font=f, fill=fill)
        y += gap
    return y


def card(lines, sub_lines=None, logo=False, progress=1.0):
    im = aurora()
    d = ImageDraw.Draw(im)
    f = font(74, True)
    y = H // 2 - 48 * len(lines) - (40 if sub_lines else 0)
    if logo:
        w0 = d.textlength(lines[0], font=f)
        d.ellipse(((W - w0) / 2 - 48, y + 32, (W - w0) / 2 - 24, y + 56), fill=TEAL)
        y = draw_center_lines(d, lines, y, f, progress=1.0)
    else:
        y = draw_center_lines(d, lines, y, f, progress=progress)
    if sub_lines:
        fs = font(36)
        y += 18
        for i, sub in enumerate(sub_lines):
            if progress < (0.25 if i == 0 else 0.75):
                break
            w = d.textlength(sub, font=fs)
            d.text(((W - w) / 2, y), sub, font=fs, fill=MUTED if i == 0 else TEAL)
            y += 52
    return im


FILE_LINES = None
def file_card(offset, progress=1.0):
    """A real hospital price file scrolling under the headline."""
    global FILE_LINES
    if FILE_LINES is None:
        src = HERE.parent / "data" / "raw" / "hpt" / "sutterhealth.org" / "940562680-1740348929_california-pacific-medical-center-van-ness-campus_standardcharges.csv"
        FILE_LINES = []
        with open(src, encoding="utf-8-sig", errors="replace") as f:
            for i, line in enumerate(f):
                if i >= 400:
                    break
                FILE_LINES.append(line.rstrip()[:300])
    im = Image.new("RGB", (W, H), NAVY)
    d = ImageDraw.Draw(im)
    mono = ImageFont.truetype("/System/Library/Fonts/SFNSMono.ttf", 15)
    start = int(offset)
    y = -int((offset - start) * 17)
    for line in FILE_LINES[start:start + 70]:
        d.text((40, y), line, font=mono, fill=(110, 130, 158))
        y += 17
    im = Image.blend(im, aurora(), 0.62)
    d = ImageDraw.Draw(im)
    f = font(66, True)
    draw_center_lines(d, ["By law, every hospital publishes their prices,", "but they make it unreadable for people."], H / 2 - 80, f, progress=progress)
    return im


# ---------------------------------------------------------------- facts from the take
tile_rows = rows("panel_places")
me_row = next((r for r in tile_rows if r["me"]), None)
priced = [r for r in tile_rows if money(r.get("val") or "")]
worst = max(priced, key=lambda r: int(money(r["val"]).replace("$", "").replace(",", ""))) if priced else None
rec_name = (me_row or {}).get("name", "").strip() or "UCSF Medical Center"
rec_val = money((me_row or {}).get("val")) or "$3,156"
worst_val = money((worst or {}).get("val")) or "$7,191"
after_rows = rows("places_after")
target_row = next((r for r in rows("agent_mid") if r.get("ccn") == "050211"), None) or next((r for r in rows("agent_screen") if r.get("ccn") == "050211"), None)
print("facts:", rec_name, rec_val, worst_val)

t_type = m["send"] - (len("I have a broken arm") * 0.06 + 0.7)
q0 = m.get("questions_0", m["send"] + 40)

# ---------------------------------------------------------------- shots
# kind: card | file | real; dur seconds; a,b take times; zoom (marker,key,factor_end); hl (marker,key) or ('row', marker, pred); cap text; vo id
S = []
def shot(**k): S.append(k)

shot(kind="card", dur=6.0, lines=["When you need healthcare, but don't know", "where to go or how much it will cost."], vo="s1")
shot(kind="file", dur=8.0, vo="s2")
shot(kind="real", dur=2.2, a=t_type, b=m["send"], cap="Say what happened.", vo="s3")
shot(kind="real", dur=9.5, a=m["send"], b=q0 - 0.3, cap="Pine's agent builds the bill.")
shot(kind="real", dur=6.0, a=q0 + 0.4, b=q0 + 5.6, cap=SCRIPT["s4"], vo="s4")
TK = "tile_settled" if "tile_settled" in R else "tile"
shot(kind="real", dur=5.5, a=m.get("tile_settled", m["tile"] + 1.2) + 0.1, b=m.get("tile_settled", m["tile"] + 1.2) + 0.2, cap=f"{rec_name} · {rec_val}", vo="s5", hl=(TK, "you"), zoom=(TK, "tile", 1.3))
shot(kind="real", dur=3.0, a=m["panel_breakdown"] + 0.8, b=m["panel_breakdown"] + 0.9, cap="What that covers.", vo="s6a", hl=("panel_breakdown", "stack"), zoom=("panel_breakdown", "breakdown", 1.25))
shot(kind="real", dur=4.0, a=m["panel_places"] + 1.0, b=m["panel_places"] + 1.1, cap=f"{rec_val} here.", vo="s6b", hl=("row", "panel_places", lambda r: r["me"]), zoom=("panel_places", "list", 1.15))
shot(kind="real", dur=4.0, a=m["panel_places"] + 1.0, b=m["panel_places"] + 1.1, cap=f"{worst_val} a couple of miles away.", hl=("row", "panel_places", lambda r: worst and r["name"] == worst["name"]), zoom=("panel_places", "list", 1.15))
shot(kind="real", dur=2.5, a=m["map"] + 1.0, b=m["map"] + 2.5, zoom=("map", "map", 1.2))
shot(kind="real", dur=5.0, a=m["panel_plans"] + 2.0, b=m["panel_plans"] + 2.1, cap="Same hospital. Different plans.", vo="s7", hl=("panel_plans", "plansPanel"), zoom=("panel_plans", "plansH", 1.25))
shot(kind="real", dur=3.0, a=m.get("target_row", m["find_click"] - 2.4) + 0.2, b=m["find_click"] - 0.1, cap="No price list.", vo="s8a", hl=("row", "find_click", lambda r: r.get("ccn") == "050211"), zoom=("find_click", "list", 1.1))
shot(kind="real", dur=3.0, a=m["agent_screen"] + 0.1, b=m["agent_screen"] + 1.2, cap="Pine goes to the website.", hl=("row", "agent_screen", lambda r: r.get("ccn") == "050211"), zoom=("agent_screen", "list", 1.15))
shot(kind="real", dur=9.0, a=m["agent_screen"] + 0.5, b=m["repriced"] - 0.5, cap="Finds the file. Downloads it.", hl=("row", "agent_mid", lambda r: r.get("ccn") == "050211"), zoom=("agent_mid", "list", 1.15))
shot(kind="real", dur=5.0, a=m["places_after"] + 0.2, b=m["places_after"] + 3.5, cap="Priced. It remembers how.", vo="s8b", hl=("row", "places_after", lambda r: r.get("ccn") == "050211" or (r.get("name") or "").strip().startswith("Alameda Hospital")), zoom=("places_after", "list", 1.1))
shot(kind="card", dur=7.0, lines=["Upfront"], sub_lines=["Know where to go, and what it costs, before you go.", "Built on Pine Computer"], logo=True, vo="s9")

total = sum(s["dur"] for s in S)
print("planned", round(total, 1), "s")

# ---------------------------------------------------------------- render frames
OUTF = TAKE / "out2"
OUTF.mkdir(exist_ok=True)
for f in OUTF.glob("*.jpg"):
    f.unlink()
n = 0
cursor = 0.0
vo_at = {}


def resolve_hl(hl):
    if not hl:
        return None
    if hl[0] == "row":
        for r in rows(hl[1]):
            try:
                if hl[2](r):
                    return tuple(r["rect"])
            except Exception:
                continue
        return None
    return rect(hl[0], hl[1])


for s in S:
    k = int(s["dur"] * FPS)
    if s.get("vo"):
        vo_at[s["vo"]] = cursor + 0.15
    vo_len = VO_LEN.get(s.get("vo"), 0)
    reveal_len = min(vo_len, s["dur"] * 0.8) if vo_len else min(1.4, s["dur"] * 0.6)
    if s["kind"] in ("card", "file"):
        reveal_len = vo_len or s["dur"] * 0.7
    for i in range(k):
        p = i / max(1, k - 1)
        elapsed = i / FPS
        prog = min(1.0, max(0.0, (elapsed - 0.15) / max(0.5, reveal_len * 0.92)))
        if s["kind"] == "card":
            im = card(s["lines"], s.get("sub_lines"), s.get("logo", False), progress=prog)
        elif s["kind"] == "file":
            im = file_card(offset=p * 220, progress=prog)
        else:
            t = s["a"] + (s["b"] - s["a"]) * p
            im = load(frame_at(t))
            view = None
            if s.get("zoom"):
                zm, zk, zf = s["zoom"]
                target = rect(zm, zk)
                dy = 0
                if zm == "tile" and target and "tile_settled" not in R:
                    dy = (H - target[3]) / 2 - target[1]
                    target = (target[0], target[1] + dy, target[2], target[3])
                if not target and zk == "list":
                    target = rect(zm, "where")
                if target:
                    f_now = 1.0 + (zf - 1.0) * min(1.0, p * 1.6)
                    im, view = zoom_to(im, target, f_now)
            hr = resolve_hl(s.get("hl"))
            if hr:
                if s.get("zoom") and s["zoom"][0] == "tile" and "tile_settled" not in R:
                    hr = (hr[0], hr[1] + dy, hr[2], hr[3])
                im = highlight(im, map_rect(hr, view))
        im = caption(im, s.get("cap"), progress=prog)
        n += 1
        im.save(OUTF / f"{n:06d}.jpg", quality=92)
    cursor += s["dur"]

print("frames out", n, "seconds", round(n / FPS, 1), "vo at", {k: round(v, 1) for k, v in vo_at.items()})

# ---------------------------------------------------------------- audio: voice + ducked music
inputs = ["-i", str(OUTF / "%06d.jpg")]
filters = []
for i, (vid, at) in enumerate(sorted(vo_at.items(), key=lambda kv: kv[1])):
    inputs += ["-i", str(HERE / f"v2_{vid}.mp3")]
    filters.append(f"[{i + 1}:a]adelay={int(at * 1000)}|{int(at * 1000)}[v{i}]")
nvo = len(vo_at)
music_idx = nvo + 1
inputs += ["-stream_loop", "-1", "-i", MUSIC]
dur = n / FPS
fc = ";".join(filters) + ";" + "".join(f"[v{i}]" for i in range(nvo)) + f"amix=inputs={nvo}:normalize=0,apad,asplit=2[va][vb];" \
     f"[{music_idx}:a]atrim=0:{dur:.2f},volume=0.22,afade=t=in:st=0:d=1.5,afade=t=out:st={dur - 2.5:.2f}:d=2.5[mus];" \
     f"[mus][va]sidechaincompress=threshold=0.03:ratio=8:attack=40:release=600[ducked];" \
     f"[ducked][vb]amix=inputs=2:normalize=0:duration=first[mix]"
cmd = ["ffmpeg", "-y", "-framerate", str(FPS)] + inputs + ["-filter_complex", fc, "-map", "0:v", "-map", "[mix]",
       "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", str(FPS), "-c:a", "aac", "-b:a", "192k", "-t", f"{dur:.2f}", str(OUT)]
res = subprocess.run(cmd, capture_output=True, text=True)
if res.returncode != 0:
    print(res.stderr[-1500:])
    sys.exit(1)
print("wrote", OUT)
