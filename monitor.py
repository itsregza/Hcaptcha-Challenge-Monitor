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
SEED_SECONDS = 90.0
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
  function info(el){
    return String(
      (el.getAttribute('aria-label')||'')+' '+
      (el.getAttribute('title')||'')+' '+
      (el.id||'')+' '+
      (el.className||'')+' '+
      (el.innerText||el.textContent||'')
    ).toLowerCase();
  }
  function rect(el){
    var r = el.getBoundingClientRect();
    return { x: r.left + r.width/2, y: r.top + r.height/2, w: r.width, h: r.height };
  }
  function press(el){
    try { el.scrollIntoView({block:'center', inline:'center'}); } catch (e) {}
    try { el.focus(); } catch (e2) {}
    try { el.click(); } catch (e3) {}
    try {
      el.dispatchEvent(new PointerEvent('pointerdown', {bubbles:true, cancelable:true}));
      el.dispatchEvent(new PointerEvent('pointerup', {bubbles:true, cancelable:true}));
    } catch (e4) {}
    try {
      el.dispatchEvent(new MouseEvent('mousedown', {bubbles:true, cancelable:true, view:window}));
      el.dispatchEvent(new MouseEvent('mouseup', {bubbles:true, cancelable:true, view:window}));
      el.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true, view:window}));
    } catch (e5) {}
  }
  var sels = [
    '.refresh',
    '[class*="refresh"]',
    '[class*="skip"]',
    'button[aria-label*="Skip" i]',
    'button[aria-label*="Refresh" i]',
    '[title*="Skip" i]',
    '[title*="Refresh" i]',
    '[aria-label*="Get a new challenge" i]'
  ];
  for (var i=0;i<sels.length;i++){
    try {
      var el = document.querySelector(sels[i]);
      if (!el) continue;
      var r = rect(el);
      if (r.w < 6 || r.h < 6) continue;
      press(el);
      return { ok: true, x: r.x, y: r.y, w: r.w, h: r.h };
    } catch (e) {}
  }
  var nodes = document.querySelectorAll('div,button,a,span');
  for (var j=0;j<nodes.length;j++){
    var s = info(nodes[j]);
    if (s.indexOf('refresh') === -1 && s.indexOf('skip') === -1 && s.indexOf('new challenge') === -1) continue;
    var rr = rect(nodes[j]);
    if (rr.w < 6 || rr.h < 6 || rr.w > 140 || rr.h > 140) continue;
    press(nodes[j]);
    return { ok: true, x: rr.x, y: rr.y, w: rr.w, h: rr.h };
  }
  return null;
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
    try:
        delay = float((data or {}).get("delay", DELAY))
    except (TypeError, ValueError):
        delay = DELAY
    if delay < 0:
        delay = 0.0
    try:
        seed_seconds = float((data or {}).get("seed_seconds", SEED_SECONDS))
    except (TypeError, ValueError):
        seed_seconds = SEED_SECONDS
    if seed_seconds < 0:
        seed_seconds = 0.0
    return sitekey, webhook, delay, seed_seconds


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
            s.send("Page.enable", timeout=5)
            r = s.send("Page.captureScreenshot", {"format": "png"}, timeout=6)
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
            s.send("Runtime.enable", timeout=5)
            r = s.send(
                "Runtime.evaluate",
                {"expression": CLICK_TEST, "returnByValue": True},
                timeout=5,
            )
            return bool((r.get("result") or {}).get("value"))
        finally:
            s.close()
    except Exception:
        return False


def reload_captcha(port):
    try:
        page = find_captcha_page(list_targets(port))
        if not page:
            return False
        s = attach_to_target(page)
        try:
            s.send("Page.enable", timeout=5)
            s.send("Page.reload", {"ignoreCache": True}, timeout=8)
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


def _close_jobs(jobs):
    for _, x in jobs:
        try:
            x.close()
        except Exception:
            pass


def pick_ready(jobs):
    for _, ins in jobs:
        try:
            snap = ins.snap()
        except Exception:
            continue
        if snap_ready(snap):
            return ins, snap
    return None, None


def read_stable(port, avoid=""):
    end = time.time() + 12
    last = ""
    same = 0
    jobs = []
    ins = None
    snap = None
    stale = 0
    next_beat = time.time() + 3
    try:
        while time.time() < end:
            if time.time() >= next_beat:
                print("[monitor] still reading challenge frame...", flush=True)
                next_beat = time.time() + 3
            if ins is None:
                _close_jobs(jobs)
                jobs = list_jobs(port)
                if not jobs:
                    time.sleep(0.35)
                    continue
                ins, snap = pick_ready(jobs)
                if ins is None:
                    ins = jobs[0][1]
                    snap = None
                stale = 0
            if snap is None:
                try:
                    snap = ins.snap()
                except Exception:
                    _close_jobs(jobs)
                    jobs = []
                    ins = None
                    time.sleep(0.25)
                    continue
            if not snap_ready(snap):
                snap = None
                stale += 1
                if stale >= 6:
                    ins = None
                time.sleep(0.25)
                continue
            stale = 0
            prompt = prompt_from_snap(ins, snap)
            snap = None
            if not prompt_is_challenge(prompt):
                same = 0
                last = ""
                time.sleep(0.25)
                continue
            if avoid and prompt == avoid:
                try:
                    spot = ins.run_js(CLICK_SKIP)
                    if isinstance(spot, dict) and spot.get("x") is not None:
                        ins.click_xy(float(spot["x"]), float(spot["y"]))
                except Exception:
                    pass
                _close_jobs(jobs)
                jobs = []
                ins = None
                same = 0
                last = ""
                time.sleep(0.9)
                continue
            if prompt == last:
                same += 1
            else:
                last = prompt
                same = 1
            if same >= 1:
                png = b""
                try:
                    png = ins.shot_png()
                except Exception:
                    pass
                return prompt, png
            time.sleep(0.2)
        return "", b""
    finally:
        _close_jobs(jobs)


def dump_targets(port):
    try:
        ts = list_targets(port)
    except Exception as e:
        print(f"[monitor] cant list targets: {e}", flush=True)
        return
    print(f"[monitor] {len(ts)} cdp targets", flush=True)
    for t in ts[:15]:
        u = (t.get("url") or "")[:140]
        print(f"[monitor]  {t.get('type')}: {u}", flush=True)


def skip_current(port):
    jobs = list_jobs(port)
    ok = False
    try:
        for _, ins in jobs:
            try:
                spot = ins.run_js(CLICK_SKIP)
            except Exception as e:
                print(f"[monitor] skip js fail: {e}", flush=True)
                spot = None
            if isinstance(spot, dict) and spot.get("ok"):
                ok = True
                #print(f"[monitor] skip click {float(spot.get('x') or 0):.0f},{float(spot.get('y') or 0):.0f}",flush=True,)
                try:
                    ins.click_xy(float(spot["x"]), float(spot["y"]))
                except Exception:
                    pass
            else:
                print("[monitor] skip button not found in frame", flush=True)
    finally:
        _close_jobs(jobs)
    return ok


def advance(port, prev, delay=DELAY):
    wait = delay if delay > 0 else 1.2
    for i in range(4):
        skip_current(port)
        time.sleep(1.2)
        jobs = list_jobs(port)
        try:
            if not jobs:
                print(f"[monitor] after skip: no frame (try {i+1})", flush=True)
            else:
                ins, snap = pick_ready(jobs)
                if ins is None:
                    ins, snap = jobs[0][1], {}
                prompt = ""
                if snap_ready(snap):
                    prompt = prompt_from_snap(ins, snap)
                if prompt_is_challenge(prompt) and prompt != prev:
                    print("[monitor] got next challenge after skip", flush=True)
                    return prompt
                if prompt_is_challenge(prompt) and prompt == prev:
                    print(f"[monitor] known: {prompt[:100]}", flush=True)
                else:
                    print(f"[monitor] no usable challenge yet (try {i+1})", flush=True)
        finally:
            _close_jobs(jobs)
        if i < 3:
            print(f"[monitor] waiting {wait}s before next skip", flush=True)
            time.sleep(wait)
    print("[monitor] skip stuck, remounting", flush=True)
    kick(port)
    time.sleep(max(2.5, wait))
    kick(port)
    time.sleep(2.0)
    return ""


def watch(port, sitekey, webhook, stop, delay=DELAY, seed_seconds=SEED_SECONDS):
    seen = set()
    seeding = seed_seconds > 0
    seed_until = time.time() + seed_seconds
    seed_count = 0
    last = ""
    empty = 0
    pending = ""
    last_cdp_warn = 0.0
    print(f"[monitor] watching sitekey={sitekey} cdp={port} delay={delay}s", flush=True)
    kick(port)

    while not stop.is_set():
        if not cdp_alive("127.0.0.1", port):
            now = time.time()
            if now - last_cdp_warn > 5:
                print(f"[monitor] chrome debug port {port} not responding", flush=True)
                last_cdp_warn = now
            time.sleep(0.8)
            continue

        png = b""
        if pending:
            prompt = pending
            pending = ""
        else:
            try:
                prompt, png = read_stable(port, avoid=last)
            except Exception as e:
                print(f"[monitor] {e}", flush=True)
                time.sleep(DELAY)
                continue

        if not prompt:
            empty += 1
            print(f"[monitor] no challenge, retry {empty}", flush=True)
            if empty == 1 or empty % 2 == 0:
                dump_targets(port)
            if last:
                print(f"[monitor] waiting {delay}s before retry skip", flush=True)
                if delay > 0:
                    time.sleep(delay)
                nxt = advance(port, last, delay=delay)
                if nxt:
                    pending = nxt
                    empty = 0
                    continue
            elif empty >= 2:
                kick(port)
            if empty >= 5:
                print("[monitor] reloading page", flush=True)
                reload_captcha(port)
                empty = 0
                time.sleep(3.0)
                kick(port)
            time.sleep(DELAY)
            continue

        empty = 0
        if not prompt_is_challenge(prompt):
            print(f"[monitor] ignoring non-challenge text: {prompt[:80]}", flush=True)
            if delay > 0:
                time.sleep(delay)
            skip_current(port)
            continue

        if seeding and time.time() >= seed_until:
            seeding = False
            print(f"[monitor] seed window done ({seed_count} known), watching for new challenges", flush=True)

        if prompt not in seen:
            seen.add(prompt)
            if seeding:
                seed_count += 1
                print(f"[monitor] seed {seed_count}: {prompt[:100]}", flush=True)
            else:
                print(f"[monitor] new: {prompt[:100]}", flush=True)
                if not png:
                    jobs = []
                    try:
                        jobs = list_jobs(port)
                        if jobs:
                            png = jobs[0][1].shot_png()
                    except Exception:
                        png = b""
                    finally:
                        _close_jobs(jobs)
                ping(webhook, sitekey, prompt, png)
        else:
            print(f"[monitor] known: {prompt[:100]}", flush=True)

        last = prompt
        if delay > 0:
            print(f"[monitor] waiting {delay}s", flush=True)
            time.sleep(delay)
        #print("[monitor] skipping to next", flush=True)
        nxt = advance(port, last, delay=delay)
        if nxt:
            pending = nxt


def launch_chrome(url, port):
    exe = chrome_path()
    profile = tempfile.mkdtemp(prefix="hcaptcha_monitor_")
    return subprocess.Popen(
        [
            exe,
            f"--user-data-dir={profile}",
            f"--remote-debugging-port={port}",
            "--remote-debugging-address=127.0.0.1",
            "--remote-allow-origins=*",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            f"--app={url}",
            "--window-size=520,640",
        ]
    )


def main():
    try:
        sitekey, webhook, delay, seed_seconds = load_config()
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
    if os.environ.get("HCAPTCHA_MONITOR_PORT"):
        port = int(os.environ["HCAPTCHA_MONITOR_PORT"])
    else:
        port = free_port()
    print(f"[monitor] starting chrome debug on {port}", flush=True)
    proc = launch_chrome(url, port)
    stop = threading.Event()
    try:
        targets = wait_for_cdp(port=port, timeout=45.0)
        print(f"[monitor] cdp up, {len(targets)} targets", flush=True)
        time.sleep(3.5)
        threading.Thread(
            target=watch,
            args=(port, sitekey, webhook, stop, delay, seed_seconds),
            daemon=True,
        ).start()
        #print(f"[monitor] captcha window -> {url}", flush=True)
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
