from __future__ import annotations

from typing import Any

from .cdp import attach_to_target, find_demo_page, list_targets

CHALLENGE = "frame=challenge"

SNAPSHOT_JS = r"""
(function() {
  function rect(el) {
    var r = el.getBoundingClientRect();
    return { x: r.left, y: r.top, w: r.width, h: r.height };
  }
  function bgUrl(el) {
    try {
      var s = getComputedStyle(el).backgroundImage;
      if (!s || s === "none") return "";
      var i = s.indexOf("url(");
      if (i < 0) return "";
      var j = i + 4;
      while (j < s.length && s.charAt(j) === " ") j++;
      var q = s.charAt(j);
      if (q === "\"" || q === "'") {
        var qend = s.indexOf(q, j + 1);
        if (qend < 0) return "";
        return s.slice(j + 1, qend);
      }
      var end = s.indexOf(")", j);
      if (end < 0) return "";
      return s.slice(j, end).trim();
    } catch (e) { return ""; }
  }
  var rawHref = "";
  try { rawHref = String(location.href || ""); } catch (e1) {}
  var out = { loc: { href: rawHref }, texts: [], buttons: [], bg_els: [] };
  var seenText = {};
  function addText(t) {
    t = String(t || "").trim();
    if (t.length < 3 || t.length > 500) return;
    if (seenText[t]) return;
    seenText[t] = true;
    out.texts.push(t);
  }
  var nodes = document.querySelectorAll("div,span,p,h1,h2,h3,label,strong");
  for (var i = 0; i < nodes.length; i++) {
    var el = nodes[i];
    if (el.children && el.children.length) continue;
    addText(el.textContent || "");
  }
  var btnSel = "[role='button'],button,.button";
  var seenBtn = {};
  function addBtn(el) {
    var r = rect(el);
    if (r.w < 2 || r.h < 2) return;
    var k = el.className + "|" + r.x + "|" + r.y;
    if (seenBtn[k]) return;
    seenBtn[k] = true;
    out.buttons.push({
      cls: String(el.className || ""),
      aria: String(el.getAttribute("aria-label") || ""),
      text: String((el.textContent || "")).trim().slice(0, 200),
      title: String(el.getAttribute("title") || ""),
      x: r.x, y: r.y, w: r.w, h: r.h
    });
  }
  var b1 = document.querySelectorAll(btnSel);
  for (var j = 0; j < b1.length; j++) addBtn(b1[j]);
  var tasks = document.querySelectorAll("[class*='task']");
  for (var k = 0; k < tasks.length; k++) {
    if (!tasks[k].matches(btnSel)) addBtn(tasks[k]);
  }
  var seenBg = {};
  var all = document.querySelectorAll("*");
  for (var n = 0; n < all.length; n++) {
    var e = all[n];
    var u = bgUrl(e);
    if (!u || u.indexOf("imgs.hcaptcha.com") === -1) continue;
    var rr = rect(e);
    if (rr.w < 10 || rr.h < 10) continue;
    var bk = u + "|" + Math.round(rr.x) + "|" + Math.round(rr.y);
    if (seenBg[bk]) continue;
    seenBg[bk] = true;
    out.bg_els.push({ bgUrl: u, cls: String(e.className || ""), x: rr.x, y: rr.y, w: rr.w, h: rr.h });
  }
  return out;
})()
"""


def challenge_oopifs(targets: list[dict]) -> list[dict]:
    out = []
    for t in targets:
        if not isinstance(t, dict):
            continue
        if not t.get("webSocketDebuggerUrl"):
            continue
        u = (t.get("url") or "").lower()
        if CHALLENGE in u:
            out.append(t)
            continue
        if "hcaptcha.com" in u and "challenge" in u and "checkbox" not in u:
            out.append(t)
    return out


def challenge_frames(node: dict) -> list[tuple[str, str]]:
    out = []
    frame = node.get("frame") or {}
    url = (frame.get("url") or "").strip()
    if CHALLENGE in url.lower():
        fid = frame.get("id")
        if fid:
            out.append((str(fid), url))
    for ch in node.get("childFrames") or []:
        out.extend(challenge_frames(ch))
    return out


class Frame:
    def __init__(self, sess, frame_id=None, boot_href=""):
        self._sess = sess
        self._frame_id = frame_id
        self._ctx = None
        self._boot = (boot_href or "").strip()

    @classmethod
    def from_oopif(cls, target: dict):
        s = attach_to_target(target)
        s.send("Page.enable", timeout=8)
        s.send("Runtime.enable", timeout=8)
        return cls(s, boot_href=(target.get("url") or "").strip())

    def _ctx_id(self):
        if self._frame_id is None:
            return None
        if self._ctx is None:
            r = self._sess.send(
                "Page.createIsolatedWorld",
                {
                    "frameId": self._frame_id,
                    "worldName": "hcaptcha-monitor",
                    "grantUniveralAccess": True,
                },
                timeout=8,
            )
            self._ctx = int(r["executionContextId"])
        return self._ctx

    def snap(self) -> dict[str, Any]:
        params = {
            "expression": SNAPSHOT_JS.strip(),
            "returnByValue": True,
            "awaitPromise": False,
        }
        ctx = self._ctx_id()
        if ctx is not None:
            params["contextId"] = ctx
        r = self._sess.send("Runtime.evaluate", params, timeout=8)
        res = r.get("result") or {}
        if res.get("subtype") == "error" or "exceptionDetails" in res:
            return {}
        out = res.get("value") if isinstance(res.get("value"), dict) else {}
        if not out:
            return out
        loc = out.get("loc")
        if not isinstance(loc, dict):
            loc = {}
            out["loc"] = loc
        href = (loc.get("href") or "").strip()
        if CHALLENGE not in href.lower() and CHALLENGE in self._boot.lower():
            loc["href"] = self._boot
        return out

    def run_js(self, expr: str, timeout: float = 6.0):
        params = {"expression": expr, "returnByValue": True, "awaitPromise": False}
        ctx = self._ctx_id()
        if ctx is not None:
            params["contextId"] = ctx
        r = self._sess.send("Runtime.evaluate", params, timeout=timeout)
        return (r.get("result") or {}).get("value")

    def click_xy(self, x: float, y: float) -> None:
        for typ, buttons in (
            ("mouseMoved", 0),
            ("mousePressed", 1),
            ("mouseReleased", 0),
        ):
            params = {
                "type": typ,
                "x": float(x),
                "y": float(y),
                "button": "left" if typ != "mouseMoved" else "none",
                "clickCount": 1,
            }
            if typ != "mouseMoved":
                params["buttons"] = buttons
            self._sess.send("Input.dispatchMouseEvent", params, timeout=2)

    def shot_png(self) -> bytes:
        import base64

        try:
            r = self._sess.send("Page.captureScreenshot", {"format": "png"}, timeout=6)
        except Exception:
            return b""
        raw = r.get("data") or ""
        return base64.b64decode(raw) if raw else b""

    def close(self) -> None:
        try:
            self._sess.close()
        except Exception:
            pass


def list_jobs(port: int) -> list[tuple[str, Frame]]:
    try:
        targets = list_targets(port)
    except Exception:
        return []

    jobs = []
    oopifs = list(reversed(challenge_oopifs(targets)))
    for ch in oopifs[:2]:
        tid = str(ch.get("id") or ch.get("url") or "")
        try:
            jobs.append((f"{port}:oopif:{tid}", Frame.from_oopif(ch)))
        except Exception:
            continue
    if jobs:
        return jobs

    page = find_demo_page(targets)
    if page is None:
        return jobs
    s = None
    try:
        s = attach_to_target(page)
        s.send("Page.enable", timeout=8)
        s.send("Runtime.enable", timeout=8)
        tree = s.send("Page.getFrameTree", timeout=8)
        frames = list(reversed(challenge_frames(tree.get("frameTree") or {})))
        for fid, furl in frames[:2]:
            try:
                sess = attach_to_target(page)
                sess.send("Page.enable", timeout=8)
                sess.send("Runtime.enable", timeout=8)
                jobs.append((f"{port}:frame:{fid}", Frame(sess, frame_id=fid, boot_href=furl)))
            except Exception:
                continue
    except Exception:
        pass
    finally:
        if s is not None:
            try:
                s.close()
            except Exception:
                pass
    return jobs
