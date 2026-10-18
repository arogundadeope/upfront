"""Cut a take into the 90-second video: time-lapse the waits, add title and close cards and captions, lay the voiceover.

usage: edit.py TAKE_NAME [out.mp4]
Needs: video/take_<name>/{frames, frames.jsonl, markers.json}, video/vo_p*.mp3
"""
import json, subprocess, sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
TAKE = HERE / f"take_{sys.argv[1] if len(sys.argv) > 1 else 'a'}"
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else HERE / "upfront_demo.mp4"
FPS = 30
W, H = 1920, 1080
NAVY = (10, 22, 40)
TEAL = (0, 168, 140)
WHITE = (255, 255, 255)
MUTED = (160, 170, 185)

frames = [json.loads(l) for l in open(TAKE / "frames.jsonl")]
markers = json.load(open(TAKE / "markers.json"))
print("frames", len(frames), "markers", markers)


def font(size, bold=False):
    for name in (["/System/Library/Fonts/SFNS.ttf", "/System/Library/Fonts/Supplemental/Avenir Next.ttc", "/System/Library/Fonts/HelveticaNeue.ttc"]):
        try:
            return ImageFont.truetype(name, size, index=(5 if bold and name.endswith("Avenir Next.ttc") else 0))
        except Exception:
            continue
    return ImageFont.load_default()


def frame_at(t):
    """The screencast frame visible at take-time t."""
    lo, hi = 0, len(frames) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if frames[mid]["ts"] <= t:
            lo = mid
        else:
            hi = mid - 1
    return TAKE / "frames" / frames[lo]["file"]


def card(lines, sub=None, logo=False, fname="card.png"):
    im = Image.new("RGB", (W, H), NAVY)
    d = ImageDraw.Draw(im)
    # soft teal glow top-left, like the page
    glow = Image.new("RGB", (W, H), NAVY)
    gd = ImageDraw.Draw(glow)
    gd.ellipse((-700, -700, 700, 500), fill=(14, 52, 58))
    glow = glow.resize((W // 16, H // 16)).resize((W, H), Image.BILINEAR)
    im = Image.blend(im, glow, 0.6)
    d = ImageDraw.Draw(im)
    y = H // 2 - 40 * len(lines)
    for i, line in enumerate(lines):
        f = font(68 if i == 0 or not logo else 52, bold=True)
        w = d.textlength(line, font=f)
        d.text(((W - w) / 2, y), line, font=f, fill=WHITE)
        y += 92
    if sub:
        f = font(34)
        w = d.textlength(sub, font=f)
        d.text(((W - w) / 2, y + 20), sub, font=f, fill=MUTED)
    if logo:
        f0 = font(68, bold=True)
        w0 = d.textlength(lines[0], font=f0)
        y0 = H // 2 - 40 * len(lines)
        d.ellipse((W / 2 - w0 / 2 - 44, y0 + 30, W / 2 - w0 / 2 - 22, y0 + 52), fill=TEAL)
    p = TAKE / fname
    im.save(p, quality=95)
    return p


def caption(img_path, text, fname):
    im = Image.open(img_path).convert("RGB")
    if im.size != (W, H):
        im = im.resize((W, H))
    d = ImageDraw.Draw(im)
    f = font(40, bold=True)
    w = d.textlength(text, font=f)
    pad = 26
    x, y = (W - w) / 2, H - 150
    d.rounded_rectangle((x - pad, y - 16, x + w + pad, y + 64), radius=18, fill=(10, 22, 40))
    d.text((x, y), text, font=f, fill=WHITE)
    p = TAKE / fname
    im.save(p)
    return p


# ------------------------------------------------------------------ timeline
# Each segment: (take_start, take_end, out_duration) -> frames sampled with uniform speed; or ("card", path, duration)
m = markers
seg = []
def real(a, b, speed=1.0):
    seg.append(("real", m[a] if isinstance(a, str) else a, m[b] if isinstance(b, str) else b, speed))
def hold(path, dur):
    seg.append(("card", str(path), dur))
def still(t, dur):
    seg.append(("still", t, dur))

t_type = m["send"] - (len("I have a broken arm") * 0.06 + 0.7)   # typing starts this long before send

card1 = card(["When you need care,", "you don't know where to go", "or what it will cost."], fname="card1.jpg")


def file_card(fname="card3.jpg"):
    """A real slice of a hospital's published price file, as the viewer would find it."""
    src = HERE.parent / "data" / "raw" / "hpt" / "sutterhealth.org" / "940562680-1740348929_california-pacific-medical-center-van-ness-campus_standardcharges.csv"
    lines = []
    with open(src, encoding="utf-8-sig", errors="replace") as f:
        for i, line in enumerate(f):
            if i >= 60:
                break
            lines.append(line.rstrip()[:260])
    im = Image.new("RGB", (W, H), NAVY)
    d = ImageDraw.Draw(im)
    mono = ImageFont.truetype("/System/Library/Fonts/SFNSMono.ttf", 15)
    y = 40
    for line in lines:
        d.text((40, y), line, font=mono, fill=(120, 140, 165))
        y += 17
    # darken and put the headline over it
    overlay = Image.new("RGB", (W, H), NAVY)
    im = Image.blend(im, overlay, 0.45)
    d = ImageDraw.Draw(im)
    f = font(62, bold=True)
    for k, t in enumerate(["Every hospital publishes a file like this.", "Its own website. Its own format. Millions of rows."]):
        w = d.textlength(t, font=f)
        d.text(((W - w) / 2, H / 2 - 70 + k * 92), t, font=f, fill=WHITE)
    p = TAKE / fname
    im.save(p)
    return p


card3 = file_card()
card2 = card(["Upfront"], sub="Know where to go, and what it costs, before you go.   Built on Pine Computer", logo=True, fname="card2.jpg")

hold(card1, 4.3)                                 # p1
hold(card3, 12.0)                                # p2 first half: the raw file
still(m["page"] - 0.6, 7.5)                      # p2 second half: the empty page, before typing
real(t_type, "send", 1.0)                        # typing ~2.8s
real("send", "tile", None)                       # agent working: fit into ~20s (p2 tail + p3)
real("tile", "panel_breakdown", 1.8)
real("panel_breakdown", "panel_places", 1.8)
real("panel_places", "map", 1.4)
real("map", "panel_plans", 1.9)
real("panel_plans", "radius_10", 1.9)
real("radius_10", "find_click", 1.6)
real("find_click", "agent_screen", None)         # until the agent's screen appears: fit ~6s
real("agent_screen", "repriced", None)           # the fetch: fit ~12s
real("repriced", "places_after", 1.3)
still(m["places_after"] + 3.5, 1.0)
hold(card2, 5.5)

# fit the variable segments to target durations
TARGET = {("send", "tile"): 15.5, ("find_click", "agent_screen"): 4.0, ("agent_screen", "repriced"): 10.0}
plan = []
for s in seg:
    if s[0] == "real":
        _, a, b, speed = s
        dur_real = max(0.1, b - a)
        if speed is None:
            key = next(k for k in TARGET if m[k[0]] == a and m[k[1]] == b)
            out = TARGET[key]
        else:
            out = dur_real / speed
        plan.append(("real", a, b, out))
    else:
        plan.append(s)

total = sum(p[3] if p[0] == "real" else p[2] for p in plan)
print("planned duration", round(total, 1))

# ------------------------------------------------------------------ frames out
OUTF = TAKE / "out"
OUTF.mkdir(exist_ok=True)
for f in OUTF.glob("*.jpg"):
    f.unlink()
n = 0
cursor = 0.0
caps = []  # (start, end, text)
import os
_capcache = {}
def emit(path, cap=None):
    global n
    n += 1
    dst = OUTF / f"{n:06d}.jpg"
    if cap:
        key = (str(path), cap)
        if key not in _capcache:
            _capcache[key] = caption(path, cap, f"cap_{len(_capcache):04d}.jpg")
        path = _capcache[key]
    os.link(path, dst)

for p in plan:
    if p[0] == "card":
        for _ in range(int(p[2] * FPS)):
            emit(Path(p[1]))
        cursor += p[2]
    elif p[0] == "still":
        path = frame_at(p[1])
        for _ in range(int(p[2] * FPS)):
            emit(path)
        cursor += p[2]
    else:
        _, a, b, out = p
        k = int(out * FPS)
        cap = None
        if (a, b) == (m["agent_screen"], m["repriced"]):
            cap = "Pine's agent is on the hospital's website, finding the price file"
        if (a, b) == (m["repriced"], m["places_after"]):
            cap = "Downloaded, parsed, re-priced. The Computer keeps the skill it learned."
        for i in range(k):
            t = a + (b - a) * (i / max(1, k))
            emit(frame_at(t), cap)
        cursor += out

print("frames out", n, "seconds", round(n / FPS, 1))

# ------------------------------------------------------------------ audio: place each paragraph at its beat
beats = {"p1": 0.3, "p2": 4.8}
# p3 starts when typing starts; p4 at the tile; p5 at find_click; p6 at the close card
acc = 0.0
starts = {}
for p in plan:
    d = p[3] if p[0] == "real" else p[2]
    if p[0] == "real" and p[1] == t_type:
        starts["p3"] = acc
    if p[0] == "real" and p[1] == m["tile"]:
        starts["p4"] = acc
    if p[0] == "real" and p[1] == m["find_click"]:
        starts["p5"] = acc
    if p[0] == "card" and "card2" in p[1]:
        starts["p6"] = acc - 1.0
    acc += d
beats.update(starts)
print("beats", {k: round(v, 1) for k, v in beats.items()})

inputs, filters, mixes = [], [], []
for i, (pid, at) in enumerate(sorted(beats.items(), key=lambda kv: kv[1])):
    inputs += ["-i", str(HERE / f"vo_{pid}.mp3")]
    filters.append(f"[{i + 1}:a]adelay={int(at * 1000)}|{int(at * 1000)},volume=1.0[a{i}]")
    mixes.append(f"[a{i}]")
fc = ";".join(filters) + ";" + "".join(mixes) + f"amix=inputs={len(mixes)}:normalize=0,apad[mix]"

subs = TAKE / "caps.ass"
with open(subs, "w") as f:
    f.write("[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n")
    f.write("Style: Cap,Helvetica Neue,44,&H00FFFFFF,&H00FFFFFF,&H00281600,&H80281600,1,0,0,0,100,100,0,0,3,14,0,2,80,80,70,1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    def ts(x):
        h = int(x // 3600); mn = int(x % 3600 // 60); s = x % 60
        return f"{h}:{mn:02d}:{s:05.2f}"
    for a, b, text in caps:
        f.write(f"Dialogue: 0,{ts(a)},{ts(b)},Cap,,0,0,0,,{text}\n")

cmd = ["ffmpeg", "-y", "-framerate", str(FPS), "-i", str(OUTF / "%06d.jpg")] + inputs + [
    "-filter_complex", fc, "-map", "0:v", "-map", "[mix]",
    "-vf", f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,format=yuv420p",
    "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-r", str(FPS), "-c:a", "aac", "-b:a", "160k", "-t", f"{n / FPS:.2f}", str(OUT)]
print(" ".join(cmd)[:400])
subprocess.run(cmd, check=True, capture_output=True)
print("wrote", OUT)
