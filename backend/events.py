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
        self.gpu_hot = ev.get("gpu_temp_warn", 85)
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
            snap["nodes"][nid] = {"up": n.get("up"),
                                  "gpu_temp": (n.get("gpu") or {}).get("temp")}
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
            t = n.get("gpu_temp")
            if t is not None:
                if t >= self.gpu_hot and nid not in self.hot:
                    self.hot.add(nid)
                    ev.append(make_event(now, nid, "detector", "thermal", "warn",
                                         f"{nid} GPU hot: {t:.0f}°C",
                                         f"threshold {self.gpu_hot}°C"))
                elif t < self.gpu_hot - 5 and nid in self.hot:
                    self.hot.discard(nid)
                    ev.append(make_event(now, nid, "detector", "thermal", "info",
                                         f"{nid} GPU cooled to {t:.0f}°C"))

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
                "from " + ", ".join(sorted({r.get('requester_ip') or '?' for r in rs})),
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
