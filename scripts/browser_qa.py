"""Headless Edge QA with isolated profile and actual viewport emulation."""
import base64
import argparse
import json
import subprocess
import time
import urllib.request
from pathlib import Path

from websockets.sync.client import connect

root = Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser()
parser.add_argument("--url", default="http://localhost:3000")
args = parser.parse_args()
folder = root / "data/reports"
folder.mkdir(parents=True, exist_ok=True)
edge = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
process = subprocess.Popen([edge, "--headless", "--disable-gpu", "--no-first-run", "--remote-debugging-port=9228",
                            "--user-data-dir=" + str(root / "data/browser-cdp"), "about:blank"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=subprocess.CREATE_NO_WINDOW)
try:
    pages = None
    for _ in range(50):
        try:
            with urllib.request.urlopen("http://127.0.0.1:9228/json", timeout=1) as response:
                pages = json.load(response)
            break
        except OSError:
            time.sleep(.2)
    if not pages:
        raise RuntimeError("Browser debugger did not start")
    page = next(item for item in pages if item["type"] == "page")
    with connect(page["webSocketDebuggerUrl"]) as socket:
        sequence = 0
        def command(method, params=None):
            global sequence
            sequence += 1
            socket.send(json.dumps({"id": sequence, "method": method, "params": params or {}}))
            while True:
                response = json.loads(socket.recv())
                if response.get("id") == sequence:
                    if "error" in response:
                        raise RuntimeError(response["error"]["message"])
                    return response.get("result", {})
        command("Page.enable")
        checks = []
        for label, width, height, mobile in [("desktop", 1440, 1000, False), ("mobile", 390, 844, True)]:
            command("Emulation.setDeviceMetricsOverride", {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": mobile})
            command("Page.navigate", {"url": args.url})
            time.sleep(3)
            result = command("Runtime.evaluate", {"expression": "JSON.stringify({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,hasComposer:!!document.querySelector('textarea'),title:document.title})", "returnByValue": True})
            metrics = json.loads(result["result"]["value"])
            metrics["viewport"] = label
            checks.append(metrics)
            shot = command("Page.captureScreenshot", {"format": "png"})
            (folder / (label + ".png")).write_bytes(base64.b64decode(shot["data"]))
            command("Runtime.evaluate", {"expression": "document.querySelector('.suggestions button').click()"})
            time.sleep(.2)
            filled = command("Runtime.evaluate", {"expression": "document.querySelector('textarea').value.length > 0", "returnByValue": True})
            assert filled["result"]["value"] is True
            command("Runtime.evaluate", {"expression": "document.querySelector('form.composer').requestSubmit()"})
            time.sleep(.2)
            guarded = command("Runtime.evaluate", {"expression": "document.querySelector('.composer-error')?.textContent.includes('Đăng nhập')", "returnByValue": True})
            assert guarded["result"]["value"] is True
            assert metrics["scrollWidth"] <= metrics["width"], metrics
        (folder / "browser-qa.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(checks, ensure_ascii=True))
finally:
    process.terminate()
    process.wait(timeout=15)
