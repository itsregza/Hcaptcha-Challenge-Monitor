from __future__ import annotations

import http.server
import json
import os
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from urllib.parse import quote

from lib.cdp import (
    attach_to_target,
    cdp_alive,
    find_captcha_page,
    list_targets,
    wait_for_cdp,
)
from lib.challenge import list_jobs
from lib.scrape import prompt_from_texts, prompt_is_challenge, snap_ready


def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


ROOT = app_dir()
CONFIG_PATH = os.path.join(ROOT, "config.json")

CDP_PORT = 9331
SEED = 5
DELAY = 3.0

CLICK_TEST = """
(function(){
  var b = document.getElementById('test-btn');
  if (b) { b.click(); return true; }
  return false;
})()
"""

CLICK_SKIP = """
(function(){
  function t(el){
    return String(
      (el.getAttribute('aria-label')||'')+' '+
      (el.getAttribute('title')||'')+' '+
      (el.innerText||el.textContent||'')
    ).toLowerCase();
  }
  var nodes = document.querySelectorAll('button,div[role="button"],.button,a,[class*="refresh"]');
  for (var i=0;i<nodes.length;i++){
    var s = t(nodes[i]);
    if (s.indexOf('skip')!==-1 || s.indexOf('refresh')!==-1){
      nodes[i].click();
      return true;
    }
  }
  return false;
})()
"""

PROMPT_JS = """
(function(){
  var sels = ['.prompt-text','[class*="prompt-text"]','.challenge-prompt','[class*="challenge-prompt"]','h2.prompt','.prompt'];
  for (var i=0;i<sels.length;i++){
    var el = document.querySelector(sels[i]);
    if (!el) continue;
    var t = String(el.innerText||el.textContent||'').trim();
    if (t.length >= 14) return t;
  }
  return '';
})()
"""


def load_config():
    if not os.path.isfile(CONFIG_PATH):
        raise FileNotFoundError(f"no config.json next to the exe ({CONFIG_PATH})")
    with open(CONFIG_PATH, encoding="utf-8") as f:
        data = json.load(f)
    sitekey = str((data or {}).get("sitekey") or "").strip()
    webhook = str((data or {}).get("webhook") or "").strip()
    return sitekey, webhook


def free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = int(s.getsockname()[1])
    s.close()
    return p


def serve_static(folder):
    port = free_port()

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=folder, **k)

        def log_message(self, *_a):
            return

    httpd = socketserver.TCPServer(("127.0.0.1", port), Handler)
    httpd.allow_reuse_address = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, port


def chrome_path():
    hits = [
        os.path.join(os.environ.get("ProgramFiles", ""), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
    ]
    for p in hits:
        if p and os.path.isfile(p):
            return p
    raise RuntimeError("chrome not found")


def ping(webhook, sitekey, prompt, png=b""):
    embed = {
        "title": "hcaptcha - new challenge",
        "description": prompt[:1900],
        "color": 15105570,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": f"sitekey {sitekey[:8]}..."},
    }
    try:
        import requests

        if png:
            embed["image"] = {"url": "attachment://captcha.png"}
            r = requests.post(
                webhook,
                data={"payload_json": json.dumps({"embeds": [embed]})},
                files={"files[0]": ("captcha.png", png, "image/png")},
                timeout=20,
            )
        else:
            r = requests.post(webhook, json={"embeds": [embed]}, timeout=12)
        if r.status_code >= 400:
            print(f"[monitor] webhook {r.status_code}", flush=True)
    except Exception as e:
        print(f"[monitor] webhook fail: {e}", flush=True)


def page_shot(port):
    import base64

    try:
        page = find_captcha_page(list_targets(port))
        if not page:
            return b""
        s = attach_to_target(page)
        try:
            s.send("Page.enable", timeout=10)
            r = s.send("Page.captureScreenshot", {"format": "png"}, timeout=15)
            raw = r.get("data") or ""
            return base64.b64decode(raw) if raw else b""
        finally:
            s.close()
    except Exception:
        return b""


def kick(port):
    try:
        page = find_captcha_page(list_targets(port))
        if not page:
            return False
        s = attach_to_target(page)
        try:
            s.send("Runtime.enable", timeout=10)
            s.send(
                "Runtime.evaluate",
                {"expression": CLICK_TEST, "returnByValue": True},
                timeout=10,
            )
            return True
        finally:
            s.close()
    except Exception:
        return False


def prompt_from_snap(ins, snap):
    try:
        direct = str(ins.run_js(PROMPT_JS) or "").strip()
        if prompt_is_challenge(direct):
            return direct
    except Exception:
        pass
    return prompt_from_texts(snap.get("texts") or [])


def read_stable(port, avoid=""):
    jobs = list_jobs(port)
    if not jobs:
        return "", b""
    _, ins = jobs[0]
    try:
        end = time.time() + 18
        last = ""
        same = 0
        while time.time() < end:
            try:
                snap = ins.snap()
            except Exception:
                time.sleep(0.35)
                continue
            href = ((snap.get("loc") or {}).get("href") or "").lower()
            if "frame=challenge" not in href or not snap_ready(snap):
                time.sleep(0.35)
                continue
            prompt = prompt_from_snap(ins, snap)
            if not prompt_is_challenge(prompt):
                same = 0
                last = ""
                time.sleep(0.35)
                continue
            if avoid and prompt == avoid:
                try:
                    ins.run_js(CLICK_SKIP)
                except Exception:
                    pass
                time.sleep(1.2)
                same = 0
                last = ""
                continue
            if prompt == last:
                same += 1
            else:
                last = prompt
                same = 1
            if same >= 3:
                png = b""
                try:
                    png = ins.shot_png()
                except Exception:
                    pass
                if not png:
                    png = page_shot(port)
                return prompt, png
            time.sleep(0.4)
        return "", b""
    finally:
        for _, x in jobs:
            try:
                x.close()
            except Exception:
                pass


def skip_current(port):
    for _, ins in list_jobs(port):
        try:
            ins.run_js(CLICK_SKIP)
        except Exception:
            pass
        try:
            ins.close()
        except Exception:
            pass


def watch(port, sitekey, webhook, stop):
    seen = set()
    n = 0
    last = ""
    last_kick = 0.0
    print(f"[monitor] watching sitekey={sitekey}", flush=True)

    while not stop.is_set():
        if not cdp_alive("127.0.0.1", port):
            time.sleep(0.8)
            continue

        if time.time() - last_kick > 20:
            kick(port)
            last_kick = time.time()

        try:
            prompt, png = read_stable(port, avoid=last)
        except Exception as e:
            print(f"[monitor] {e}", flush=True)
            time.sleep(DELAY)
            continue

        if not prompt:
            kick(port)
            last_kick = time.time()
            time.sleep(DELAY)
            continue

        last = prompt
        n += 1

        if prompt not in seen:
            seen.add(prompt)
            if n <= SEED:
                print(f"[monitor] seed {n}/{SEED}: {prompt[:100]}", flush=True)
            else:
                print(f"[monitor] new: {prompt[:100]}", flush=True)
                ping(webhook, sitekey, prompt, png)
        else:
            print(f"[monitor] known: {prompt[:100]}", flush=True)

        if n == SEED:
            print("[monitor] seeded, watching for new challenges", flush=True)

        skip_current(port)
        time.sleep(DELAY)


def launch_chrome(url, port):
    exe = chrome_path()
    profile = tempfile.mkdtemp(prefix="hcaptcha_monitor_")
    return subprocess.Popen(
        [
            exe,
            f"--user-data-dir={profile}",
            f"--remote-debugging-port={port}",
            "--remote-allow-origins=*",
            "--no-first-run",
            "--no-default-browser-check",
            f"--app={url}",
            f"--window-size=520,640",
        ]
    )


def main():
    try:
        sitekey, webhook = load_config()
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"bad config: {e}")
        return 1
    if not sitekey or not webhook:
        print("fill in sitekey + webhook in config.json")
        return 1
    if "discord.com/api/webhooks" not in webhook:
        print("that webhook url doesnt look like discord")
        return 1

    if not os.path.isfile(os.path.join(ROOT, "captcha.html")):
        print("missing captcha.html")
        return 2

    httpd, http_port = serve_static(ROOT)
    url = f"http://127.0.0.1:{http_port}/captcha.html?sitekey={quote(sitekey)}"
    port = int(os.environ.get("HCAPTCHA_MONITOR_PORT") or CDP_PORT)
    proc = launch_chrome(url, port)
    stop = threading.Event()
    try:
        wait_for_cdp(port=port, timeout=45.0)
        time.sleep(3.5)
        threading.Thread(target=watch, args=(port, sitekey, webhook, stop), daemon=True).start()
        print(f"[monitor] captcha window -> {url}", flush=True)
        print("[monitor] hit Open challenge if nothing pops", flush=True)
        proc.wait()
        return 0
    except KeyboardInterrupt:
        return 130
    finally:
        stop.set()
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            httpd.shutdown()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
