"""Screenshot a URL after waiting N real seconds, via Chrome DevTools Protocol on the Playwright headless shell.
usage: shot.py URL SECONDS OUT.png [HEIGHT]"""
import asyncio, base64, json, subprocess, sys, time, urllib.request, os, signal
B = os.path.expanduser("~/Library/Caches/ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-mac-arm64/chrome-headless-shell")
url, secs, out = sys.argv[1], float(sys.argv[2]), sys.argv[3]
height = int(sys.argv[4]) if len(sys.argv) > 4 else 4400
proc = subprocess.Popen([B, "--headless", "--disable-gpu", "--hide-scrollbars", "--remote-debugging-port=9333", f"--window-size=900,{height}", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(50):
        try:
            targets = json.load(urllib.request.urlopen("http://127.0.0.1:9333/json")); break
        except Exception: time.sleep(0.2)
    ws_url = [t for t in targets if t["type"] == "page"][0]["webSocketDebuggerUrl"]
    import websockets
    async def main():
        async with websockets.connect(ws_url, max_size=50_000_000) as ws:
            i = 0
            async def call(method, **params):
                nonlocal i; i += 1
                await ws.send(json.dumps({"id": i, "method": method, "params": params}))
                while True:
                    msg = json.loads(await ws.recv())
                    if msg.get("id") == i: return msg.get("result", {})
            await call("Emulation.setDeviceMetricsOverride", width=900, height=height, deviceScaleFactor=1, mobile=False)
            await call("Page.navigate", url=url)
            await asyncio.sleep(secs)
            res = await call("Page.captureScreenshot", format="png", captureBeyondViewport=True)
            open(out, "wb").write(base64.b64decode(res["data"]))
    asyncio.run(main())
finally:
    proc.send_signal(signal.SIGTERM)
print("saved", out)
