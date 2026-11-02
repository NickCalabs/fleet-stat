"""Fleet events: a curated stream of things that changed, not a log firehose.

Two producers feed the same `events` table:
- the detector below, which diffs the pollers' snapshots every few seconds
  (model up/down, ollama load/unload, ComfyUI jobs, GPU heat, LiteLLM failures);
- POST /api/events/ingest, fed by an external collector that tails journald /
  docker logs on the nodes (the container never holds SSH keys).
"""
import asyncio
import hashlib
import logging
import re
import time

from .pollers import state

log = logging.getLogger("fleet-stat.events")

LEVELS = ("info", "warn", "error")

# Secrets that must never land in the event store, whatever the source.
_REDACT = [
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{6,}"), "sk-…"),
    (re.compile(r"\bhf_[A-Za-z0-9]{10,}"), "hf_…"),
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-]{8,}"), r"\1 …"),
    (re.compile(r"(?i)\b(api[_-]?key|token|password|secret|authorization)"
                r"([\"']?\s*[:=]\s*[\"']?)[^\s\"',;]{4,}"), r"\1\2…"),
]


def redact(text):
    if not text:
        return text
    for rx, rep in _REDACT:
        text = rx.sub(rep, text)
    return text


def make_event(ts, node, source, kind, level, title, detail=None, key=None):
    """`key` makes the id deterministic, so re-sent events dedupe on insert."""
    title = redact(str(title))[:300]
    detail = redact(str(detail))[:4000] if detail else None
    raw = key or f"{ts:.3f}|{node}|{source}|{title}|{detail or ''}"
    return {
        "id": hashlib.sha1(raw.encode()).hexdigest()[:20],
        "ts": ts, "node": node, "source": source, "kind": kind,
        "level": level if level in LEVELS else "info",
        "title": title, "detail": detail,
    }


def normalize_ingest(payload):
    """Validate collector-pushed events. Each needs ts + title at minimum."""
    out = []
    for e in (payload.get("events") or [])[:2000]:
        try:
            ts = float(e["ts"])
            title = str(e["title"])
        except (KeyError, TypeError, ValueError):
            continue
        out.append(make_event(ts, e.get("node"), e.get("source") or "log",
                              e.get("kind") or "log", e.get("level") or "info",
                              title, e.get("detail")))
    return out


class Detector:
    """Diffs successive poller snapshots and emits transition events."""

    def __init__(self, cfg, store):
        self.cfg = cfg
        self.store = store
        ev = cfg.get("events") or {}
        # Heat alerts use the driver's per-card headroom (tlimit) when the
        # exporter has it; gpu_temp_warn is only the fallback for cards without.
        self.tlimit_warn = ev.get("gpu_tlimit_warn", 5)       # °C below the card's limit
        self.gpu_hot = ev.get("gpu_temp_warn", 88)
        self.hot_hold = ev.get("gpu_hot_seconds", 60)          # must stay hot this long
        self.thermal = {}   # node -> {"since", "alerted", "cool_since", "last_alert"}
        self.slowing = set()
        self.retention_days = ev.get("retention_days", 14)
        self.prev = None
        self.fail_cursor = time.time()
        self.hot = set()

    def _snapshot(self):
        prom = state["prom"]
        snap = {"models": {}, "nodes": {}, "ollama": {}, "comfy": {}, "sources": {},
                "prom_ok": bool(prom.get("ts"))}
        for name, m in (prom.get("models") or {}).items():
            if m.get("engine") in ("vllm",):
                snap["models"][name] = (bool(m.get("up")), m.get("served_model"))
        for nid, n in (prom.get("nodes") or {}).items():
            g = n.get("gpu") or {}
            snap["nodes"][nid] = {"up": n.get("up"), "gpu_temp": g.get("temp"),
                                  "gpu_tlimit": g.get("tlimit"),
                                  "gpu_throttle": g.get("thermal_slowdown")}
        for nid, o in state["ollama"].items():
            if "loaded" in o:
                snap["ollama"][nid] = frozenset(o.get("loaded") or [])
        for name, c in state["comfy"].items():
            snap["comfy"][name] = (bool(c.get("up")), c.get("running") or 0,
                                   c.get("current_model"))
        snap["sources"]["litellm"] = state["litellm"].get("error")
        snap["sources"]["prometheus"] = prom.get("error")
        snap["sources"]["openwebui"] = state["owui"].get("error")
        return snap

    def _diff(self, a, b, now):
        ev = []
        host_of = {m["name"]: ",".join(m.get("hosts") or []) for m in self.cfg["models"]}

        for name, (up, served) in b["models"].items():
            was = a["models"].get(name)
            if was is None:
                continue
            if was[0] != up:
                ev.append(make_event(now, host_of.get(name), "detector", "model",
                                     "info" if up else "error",
                                     f"{name} {'is up' if up else 'went DOWN'}",
                                     f"serving {served}" if up and served else None))
            elif up and served and was[1] and served != was[1]:
                ev.append(make_event(now, host_of.get(name), "detector", "model", "info",
                                     f"{name} now serving {served}",
                                     f"was {was[1]}"))

        for nid, n in b["nodes"].items():
            was = a["nodes"].get(nid) or {}
            if was.get("up") is not None and n["up"] is not None and was["up"] != n["up"]:
                ev.append(make_event(now, nid, "detector", "node",
                                     "info" if n["up"] else "error",
                                     f"{nid} {'back up' if n['up'] else 'unreachable'}"))
            ev += self._thermal(nid, n, now)

        for nid, loaded in b["ollama"].items():
            was = a["ollama"].get(nid)
            if was is None:
                continue
            for m in sorted(loaded - was):
                ev.append(make_event(now, nid, "detector", "load", "info",
                                     f"{m} loaded on {nid}"))
            for m in sorted(was - loaded):
                ev.append(make_event(now, nid, "detector", "load", "info",
                                     f"{m} unloaded from {nid}"))

        for name, (up, running, model) in b["comfy"].items():
            was = a["comfy"].get(name)
            if was is None:
                continue
            node = host_of.get(name)
            if was[0] != up:
                ev.append(make_event(now, node, "detector", "model",
                                     "info" if up else "error",
                                     f"{name} {'is up' if up else 'went DOWN'}"))
            elif running and not was[1]:
                ev.append(make_event(now, node, "detector", "job", "info",
                                     f"{name} job started",
                                     f"model {model}" if model else None))
            elif was[1] and not running:
                ev.append(make_event(now, node, "detector", "job", "info",
                                     f"{name} job finished",
                                     f"model {model}" if model else None))

        for src, err in b["sources"].items():
            was = a["sources"].get(src)
            if err and not was:
                ev.append(make_event(now, None, "detector", "source", "warn",
                                     f"source {src} failing", err))
            elif was and not err:
                ev.append(make_event(now, None, "detector", "source", "info",
                                     f"source {src} recovered"))
        return ev

    def _thermal(self, nid, n, now):
        """Debounced heat alert: hot for hot_hold seconds → one warning; clears
        only after a clear margin for 2 min; at most one alert per 30 min.
        Actual thermal throttling (clocks cut for heat) is always an error."""
        ev = []
        t, margin = n.get("gpu_temp"), n.get("gpu_tlimit")
        if t is None:
            return ev
        if margin is not None:
            hot, cool = margin <= self.tlimit_warn, margin >= self.tlimit_warn + 8
            why = f"{margin:.0f}°C below its limit"
        else:
            hot, cool = t >= self.gpu_hot, t <= self.gpu_hot - 8
            why = f"threshold {self.gpu_hot}°C"
        st = self.thermal.setdefault(nid, {"since": None, "alerted": False,
                                           "cool_since": None, "last_alert": 0})
        if hot:
            st["cool_since"] = None
            st["since"] = st["since"] or now
            if (not st["alerted"] and now - st["since"] >= self.hot_hold
                    and now - st["last_alert"] > 1800):
                st["alerted"], st["last_alert"] = True, now
                ev.append(make_event(now, nid, "detector", "thermal", "warn",
                                     f"{nid} GPU hot: {t:.0f}°C ({why})"))
        else:
            st["since"] = None
            if st["alerted"] and cool:
                st["cool_since"] = st["cool_since"] or now
                if now - st["cool_since"] >= 120:
                    st["alerted"], st["cool_since"] = False, None
                    ev.append(make_event(now, nid, "detector", "thermal", "info",
                                         f"{nid} GPU cooled to {t:.0f}°C"))
        if n.get("gpu_throttle") and nid not in self.slowing:
            self.slowing.add(nid)
            ev.append(make_event(now, nid, "detector", "thermal", "error",
                                 f"{nid} GPU is THROTTLING for heat at {t:.0f}°C",
                                 "the driver is cutting clocks to protect the card"))
        elif not n.get("gpu_throttle") and nid in self.slowing:
            self.slowing.discard(nid)
            ev.append(make_event(now, nid, "detector", "thermal", "info",
                                 f"{nid} GPU thermal throttling ended"))
        return ev

    def _litellm_failures(self, now):
        """One event per (harness, model) burst of failed requests."""
        rows = [r for r in self.store.recent_requests(self.fail_cursor - 900)
                if r.get("status") in ("failure", "error")
                and (r.get("end_ts") or r["start_ts"]) > self.fail_cursor]
        if not rows:
            return []
        self.fail_cursor = max(r.get("end_ts") or r["start_ts"] for r in rows)
        labels = {h["id"]: h.get("label", h["id"]) for h in self.cfg["harnesses"]}
        groups = {}
        for r in rows:
            groups.setdefault((r["harness"], r["model_group"]), []).append(r)
        ev = []
        for (h, m), rs in groups.items():
            last = max(r["start_ts"] for r in rs)
            ev.append(make_event(
                last, None, "litellm", "request", "error",
                f"{len(rs)} failed request{'s' if len(rs) > 1 else ''}: "
                f"{labels.get(h, h)} → {m or '?'}",
                "\n".join(sorted({r["error"] for r in rs if r.get("error")})[:3]
                          + ["from " + ", ".join(sorted({r.get('requester_ip') or '?'
                                                         for r in rs}))]),
                key="fail|" + "|".join(sorted(r["request_id"] for r in rs))))
        return ev

    async def run(self, interval=5):
        self.store.add_events([make_event(time.time(), None, "detector", "system",
                                          "info", "Fleet Stat started")])
        last_prune = 0
        while True:
            await asyncio.sleep(interval)
            try:
                now = time.time()
                snap = self._snapshot()
                ev = []
                # wait for the first real poll before diffing, so boot isn't
                # reported as a wave of "is up" transitions
                if self.prev and self.prev["prom_ok"]:
                    ev += self._diff(self.prev, snap, now)
                if snap["prom_ok"]:
                    self.prev = snap
                ev += self._litellm_failures(now)
                self.store.add_events(ev)
                if now - last_prune > 3600:
                    self.store.prune_events(now - self.retention_days * 86400)
                    last_prune = now
            except Exception as e:
                log.warning("event detector failed: %s", e)
