"""Drive the live product in a headless browser and record a screencast with phase markers.

Output: video/take_<n>/frames/*.jpg, frames.jsonl (ts, file), markers.json (phase -> ts)
usage: capture.py TAKE_NAME
"""
import asyncio, base64, json, os, signal, subprocess, sys, time, urllib.request
from pathlib import Path

import websockets

B = os.path.expanduser("~/Library/Caches/ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-mac-arm64/chrome-headless-shell")
URL = "http://127.0.0.1:8010/"
W, H, SCALE = 1920, 1080, 1.0         # native 1080p frames; the page is zoomed for legibility
TAKE = Path(__file__).resolve().parent / f"take_{sys.argv[1] if len(sys.argv) > 1 else 'a'}"
FR = TAKE / "frames"
FR.mkdir(parents=True, exist_ok=True)

REQUEST = "I have a broken arm"
TARGET_CCN = "050211"   # Alameda Hospital: never fetched, within 10 miles


async def main():
    proc = subprocess.Popen([B, "--headless", "--disable-gpu", "--hide-scrollbars", "--remote-debugging-port=9444",
                             f"--window-size={W},{H}", "--force-device-scale-factor=1", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                targets = json.load(urllib.request.urlopen("http://127.0.0.1:9444/json")); break
            except Exception:
                time.sleep(0.2)
        ws_url = [t for t in targets if t["type"] == "page"][0]["webSocketDebuggerUrl"]
        async with websockets.connect(ws_url, max_size=100_000_000) as ws:
            i = 0
            pending = {}
            frames = open(TAKE / "frames.jsonl", "w")
            markers = {}
            t0 = time.time()
            nframe = 0

            async def send(method, **params):
                nonlocal i
                i += 1
                fut = asyncio.get_event_loop().create_future()
                pending[i] = fut
                await ws.send(json.dumps({"id": i, "method": method, "params": params}))
                return await fut

            async def reader():
                nonlocal nframe
                async for raw in ws:
                    msg = json.loads(raw)
                    if "id" in msg and msg["id"] in pending:
                        pending.pop(msg["id"]).set_result(msg.get("result", {}))
                    elif msg.get("method") == "Page.screencastFrame":
                        p = msg["params"]
                        nframe += 1
                        fn = FR / f"{nframe:06d}.jpg"
                        fn.write_bytes(base64.b64decode(p["data"]))
                        frames.write(json.dumps({"ts": round(time.time() - t0, 3), "file": fn.name}) + "\n")
                        asyncio.ensure_future(send("Page.screencastFrameAck", sessionId=p["sessionId"]))

            asyncio.ensure_future(reader())

            async def js(expr):
                r = await send("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
                return r.get("result", {}).get("value")

            rects = {}
            RECT_JS = """(function(){
              function r(sel){ var e=document.querySelector(sel); if(!e) return null; var b=e.getBoundingClientRect(); return [b.left,b.top,b.width,b.height]; }
              function rows(){ return Array.from(document.querySelectorAll('#list .r')).map(function(e){ var b=e.getBoundingClientRect(); return {ccn:e.getAttribute('data-ccn'), me:e.classList.contains('me'), out:e.classList.contains('out'), nofile:e.classList.contains('nofile'), name:(e.querySelector('.n')||{}).firstChild && e.querySelector('.n').firstChild.textContent, val:(e.querySelector('.v')||{}).textContent, rect:[b.left,b.top,b.width,b.height]}; }); }
              function plans(){ return Array.from(document.querySelectorAll('#plans .p')).map(function(e){ var b=e.getBoundingClientRect(); return {me:e.classList.contains('me'), name:(e.querySelector('.n')||{}).textContent, val:(e.querySelector('.v')||{}).textContent, rect:[b.left,b.top,b.width,b.height]}; }); }
              return {tile:r('#tile'), kpis:r('#tile .kpis'), you:r('#tile .kpi.you'), h1:r('#tile h1'), ask:r('.askbar'), work:r('.work:last-of-type'), screen:r('.work:last-of-type .screen'),
                      breakdown:r('.story .panel:nth-child(1)'), stack:r('#p-break-stack'), leg:r('#p-break-leg'), where:r('#where'), list:r('#list'), map:r('#map'), plansPanel:r('#plans') && r('#plans').concat([]) , plansH:r('#p-plans-h'), radius:r('#radius'),
                      rows:rows(), plans:plans(), scrollY:window.scrollY, zoom:document.documentElement.style.zoom};
            })()"""

            def mark(name):
                markers[name] = round(time.time() - t0, 3)
                print(f"[{markers[name]:7.1f}] {name}", flush=True)

            async def snap(name):
                mark(name)
                try:
                    rects[name] = await js(RECT_JS)
                except Exception as e:  # noqa: BLE001
                    rects[name] = {"error": str(e)}

            async def wait_for(expr, timeout=300, every=0.5):
                t = time.time()
                while time.time() - t < timeout:
                    if await js(expr):
                        return True
                    await asyncio.sleep(every)
                return False

            async def smooth_scroll(to_y, duration=1.2):
                await js(f"window.scrollTo({{top:{to_y}, behavior:'smooth'}}); true")
                await asyncio.sleep(duration)

            async def scroll_into(selector, block="center", duration=1.2):
                await js(f"(function(){{var e=document.querySelector({json.dumps(selector)}); if(e) e.scrollIntoView({{behavior:'smooth', block:'{block}'}}); return !!e;}})()")
                await asyncio.sleep(duration)

            await send("Emulation.setDeviceMetricsOverride", width=W, height=H, deviceScaleFactor=SCALE, mobile=False)
            await send("Page.enable")
            await send("Page.navigate", url=URL)
            await asyncio.sleep(2.0)
            await js("document.documentElement.style.zoom='1.5'; true")
            await asyncio.sleep(0.8)
            await send("Page.startScreencast", format="jpeg", quality=85, maxWidth=1920, maxHeight=1080, everyNthFrame=1)
            await asyncio.sleep(1.0)
            mark("page")

            # ---- type the request
            await js("document.getElementById('ask').focus(); true")
            for n in range(1, len(REQUEST) + 1):
                await js(f"(function(){{var i=document.getElementById('ask'); i.value={json.dumps(REQUEST[:n])}; return true;}})()")
                await asyncio.sleep(0.06)
            await asyncio.sleep(0.6)
            mark("send")
            await js("document.getElementById('send').click(); true")
            await asyncio.sleep(9); await snap("working")

            # ---- answer whatever the agent asks: prefer splint/forearm, paying cash
            async def answer_questions():
                for _round in range(3):
                    ok = await wait_for("document.querySelectorAll('.sys .seg b').length > 0 || !!document.querySelector('#tile.in')", timeout=240)
                    if not ok:
                        return
                    if await js("!!document.querySelector('#tile.in')"):
                        return
                    await snap(f"questions_{_round}")
                    await asyncio.sleep(1.5)
                    # click one option per segmented control in the LAST sys bubble
                    await js("""(function(){
                        var box = Array.from(document.querySelectorAll('.sys')).pop(); if(!box) return false;
                        var prefs = [/splint only|splint,? no|without reduction/i, /splint/i, /forearm|wrist/i, /paying cash|^cash/i];
                        var segs = Array.from(box.querySelectorAll('.seg'));
                        var cash = segs.some(function(seg){ return Array.from(seg.querySelectorAll('b')).some(function(b){ return /paying cash/i.test(b.textContent); }); });
                        var k = 0;
                        segs.forEach(function(seg){
                          var wrap = seg.closest('[data-q]'); var qid = wrap ? wrap.getAttribute('data-q') : '';
                          if (cash && qid === 'product') return;            // no plan type when paying cash
                          var bs = Array.from(seg.querySelectorAll('b')); var pick = null;
                          prefs.forEach(function(re){ if(!pick){ pick = bs.find(function(b){ return re.test(b.textContent); }); } });
                          pick = pick || bs[0];
                          setTimeout(function(){ pick.click(); }, 700*k); k++;
                        }); return true; })()""")
                    await asyncio.sleep(3.0)
                    # the page re-asks after answers; loop until the tile appears or a benefits/other prompt
                    await wait_for("!!document.querySelector('#tile.in') || Array.from(document.querySelectorAll('.sys')).pop().querySelectorAll('.seg b').length > 0 && !Array.from(document.querySelectorAll('.sys')).pop().querySelector('.seg b.on')", timeout=240)

            await answer_questions()
            await wait_for("!!document.querySelector('#tile.in')", timeout=300)
            mark("tile")
            await asyncio.sleep(2.0); await snap("tile_settled")
            await asyncio.sleep(2.5)

            # ---- scroll the story
            await scroll_into(".story .panel:nth-child(1)", "center", 2.5); await snap("panel_breakdown"); await asyncio.sleep(3.0)
            await scroll_into("#where", "start", 2.0); await snap("panel_places"); await asyncio.sleep(4.0)
            await js("document.getElementById('tg-map').click(); true"); await snap("map"); await asyncio.sleep(3.5)
            await js("document.getElementById('tg-list').click(); true"); await asyncio.sleep(1.0)
            await scroll_into("#plans", "center", 2.2); await snap("panel_plans"); await asyncio.sleep(3.0)

            # ---- widen the radius, find the target hospital, fetch live
            await scroll_into("#where", "start", 1.5)
            await js("document.getElementById('radius').click(); true"); mark("radius_10")
            await wait_for("!!document.querySelector('.r.nofile[data-ccn=\"%s\"] .mini')" % TARGET_CCN, timeout=240)
            await asyncio.sleep(1.0)
            await scroll_into('.r.nofile[data-ccn="%s"]' % TARGET_CCN, "center", 1.8)
            await snap("target_row"); await asyncio.sleep(1.5)
            await snap("find_click")
            await js('document.querySelector(\'.r.nofile[data-ccn="%s"] .mini\').click(); true' % TARGET_CCN)
            # keep the row in view while the agent works; mark first screen frame and the re-price
            await wait_for('!!document.querySelector(\'.r[data-ccn="%s"] img.rowshot\')' % TARGET_CCN, timeout=120)
            await snap("agent_screen")
            await asyncio.sleep(12); await snap("agent_mid")
            await wait_for('!document.querySelector(\'.r.nofile[data-ccn="%s"]\')' % TARGET_CCN, timeout=420)
            await snap("repriced")
            await asyncio.sleep(1.5)
            await scroll_into("#tile", "center", 1.5); mark("tile_after"); await asyncio.sleep(2.0)
            await scroll_into("#where", "start", 1.5); await snap("places_after"); await asyncio.sleep(4.0)
            await send("Page.stopScreencast")
            frames.close()
            (TAKE / "markers.json").write_text(json.dumps(markers, indent=1))
            (TAKE / "rects.json").write_text(json.dumps(rects, indent=1))
            print("frames:", nframe, "markers:", markers)
    finally:
        proc.send_signal(signal.SIGTERM)


asyncio.run(main())
