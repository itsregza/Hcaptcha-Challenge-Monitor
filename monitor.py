from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from urllib.parse import quote

from lib.cdp import (
    attach_to_target,
    cdp_alive,
    find_demo_page,
    list_targets,
    wait_for_cdp,
)
from lib.challenge import list_jobs
from lib.scrape import prompt_from_texts, prompt_is_challenge, snap_ready

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(ROOT, "config.json")

CDP_PORT = 9331
SEED = 5
DELAY = 3.0


CLICK_CHECKBOX = """
(function(){
  var el = document.querySelector('#checkbox')
    || document.querySelector('[role="checkbox"]')
    || document.querySelector('.check');
  if (!el) return false;
  el.click();
  return true;
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
        raise FileNotFoundError("no config.json (copy from config.example.json)")
    with open(CONFIG_PATH, encoding="utf-8") as f:
        data = json.load(f)
    sitekey = str((data or {}).get("sitekey") or "").strip()
    webhook = str((data or {}).get("webhook") or "").strip()
    return sitekey, webhook


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
        page = find_demo_page(list_targets(port))
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


def open_challenge(port):
    try:
        for t in list_targets(port):
            u = (t.get("url") or "").lower()
            if "frame=checkbox" not in u or not t.get("webSocketDebuggerUrl"):
                continue
            s = attach_to_target(t)
            try:
                s.send("Runtime.enable", timeout=10)
                s.send(
                    "Runtime.evaluate",
                    {"expression": CLICK_CHECKBOX, "returnByValue": True},
                    timeout=10,
                )
                return True
            finally:
                s.close()
    except Exception:
        pass
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
    last_open = 0.0
    print(f"[monitor] watching sitekey={sitekey}", flush=True)

    while not stop.is_set():
        if not cdp_alive("127.0.0.1", port):
            time.sleep(0.8)
            continue

        if time.time() - last_open > 20:
            open_challenge(port)
            last_open = time.time()

        try:
            prompt, png = read_stable(port, avoid=last)
        except Exception as e:
            print(f"[monitor] {e}", flush=True)
            time.sleep(DELAY)
            continue

        if not prompt:
            open_challenge(port)
            last_open = time.time()
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
            url,
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

    port = int(os.environ.get("HCAPTCHA_MONITOR_PORT") or CDP_PORT)
    url = f"https://accounts.hcaptcha.com/demo?sitekey={quote(sitekey)}"
    proc = launch_chrome(url, port)
    stop = threading.Event()
    try:
        wait_for_cdp(port=port, timeout=45.0)
        time.sleep(3.0)
        threading.Thread(target=watch, args=(port, sitekey, webhook, stop), daemon=True).start()
        print(f"[monitor] opened {url}", flush=True)
        print("[monitor] click the checkbox if nothing pops up", flush=True)
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


if __name__ == "__main__":
    raise SystemExit(main())
