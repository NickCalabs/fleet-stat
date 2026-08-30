import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import Body, FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import load_config
from .pollers import start_pollers, state
from .sessions import build_sessions
from .store import Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

cfg = load_config()
store = Store(os.environ.get("FC_DB", "/data/fleet.db"))

# range -> bucket size (seconds)
def _bucket_for(hours):
    if hours <= 6:
        return 900
    if hours <= 24:
        return 3600
    if hours <= 168:
        return 21600
    return 86400


@asynccontextmanager
async def lifespan(app):
    tasks = start_pollers(cfg, store)
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="Fleet Stat", lifespan=lifespan)


@app.get("/api/config")
def api_config():
    return {
        "harnesses": [
            {"id": h["id"], "label": h.get("label", h["id"])} for h in cfg["harnesses"]
        ],
        "models": [
            {"name": m["name"], "ctx": m.get("ctx"), "hosts": m.get("hosts", []),
             "cluster": bool(m.get("cluster")), "hidden": bool(m.get("hidden"))}
            for m in cfg["models"]
        ],
        "nodes": [{"id": n["id"], "label": n["label"]} for n in cfg["nodes"]],
        "langfuse_enabled": bool(cfg["sources"].get("langfuse", {}).get("enabled")),
    }


def _fleet_model(m, prom):
    out = {**prom["models"][m["name"]], "identity": m.get("identity")}
    if m.get("engine") == "comfyui":
        c = state["comfy"].get(m["name"]) or {}
        out.update({
            "up": c.get("up", False), "running": c.get("running"),
            "waiting": c.get("pending"), "served_model": c.get("current_model"),
            "comfy": {k: c.get(k) for k in ("vram_total", "vram_free", "version")},
        })
    om = m.get("ollama_model")
    if m.get("engine") == "ollama" and om:
        loaded = []
        for h in m.get("hosts", []):
            loaded.extend(state["ollama"].get(h, {}).get("loaded") or [])
        out["ollama_loaded"] = om in loaded
        out["served_model"] = om
    return out


@app.get("/api/fleet")
def api_fleet():
    prom = state["prom"]
    litellm = state["litellm"]
    owui = state["owui"]
    now = time.time()
    return {
        "ts": now,
        "nodes": [prom["nodes"].get(n["id"], {"id": n["id"], "label": n["label"],
                                              "hw": n.get("hw", ""), "up": None})
                  for n in cfg["nodes"]],
        "models": [_fleet_model(m, prom) for m in cfg["models"]
                   if prom["models"].get(m["name"])],
        "sources": {
            "prometheus": {"up": bool(prom["ts"]) and now - prom["ts"] < 60,
                           "error": prom.get("error")},
            "litellm": {"up": litellm.get("up", False), "error": litellm.get("error")},
            "openwebui": {"up": (bool(owui["ts"]) and now - owui["ts"] < 120)
                                if owui.get("enabled") else None,
                          "error": owui.get("error")},
            "langfuse": {"enabled": bool(cfg["sources"].get("langfuse", {}).get("enabled"))},
        },
    }


@app.get("/api/sessions")
def api_sessions(hours: float = Query(default=None, ge=0.1, le=336)):
    hours = hours or cfg["sessions"]["window_hours_default"]
    return {"window_hours": hours, "sessions": build_sessions(cfg, store, hours)}


@app.get("/api/usage")
def api_usage(hours: int = Query(default=24, ge=1, le=2160),
              group: str = Query(default="harness", pattern="^(harness|model)$")):
    now = time.time()
    bucket = _bucket_for(hours)
    since = (int(now) // bucket) * bucket - hours * 3600
    rows = store.usage(since, bucket, group)
    buckets = list(range(int(since), int(now) + bucket, bucket))
    series_keys = []
    if group == "harness":
        series_keys = [h["id"] for h in cfg["harnesses"]] + ["other"]
    else:
        series_keys = [m["name"] for m in cfg["models"] if not m.get("hidden")]
    idx = {b: i for i, b in enumerate(buckets)}
    series = {}
    for r in rows:
        g = r["grp"]
        if g not in series_keys:
            series_keys.append(g)
        s = series.setdefault(g, {
            "key": g,
            "tokens": [0] * len(buckets), "prompt": [0] * len(buckets),
            "completion": [0] * len(buckets), "requests": [0] * len(buckets),
            "latency": [None] * len(buckets),
        })
        i = idx.get(int(r["bucket"]))
        if i is None:
            continue
        s["tokens"][i] = r["total_tokens"]
        s["prompt"][i] = r["prompt_tokens"]
        s["completion"][i] = r["completion_tokens"]
        s["requests"][i] = r["requests"]
        s["latency"][i] = r["avg_latency_s"]
    ordered = [series[k] for k in series_keys if k in series]
    return {
        "group": group, "hours": hours, "bucket_s": bucket, "buckets": buckets,
        "series": ordered,
        "totals": store.totals(since, group),
    }


def _norm(s):
    return "".join(c for c in (s or "").lower() if c.isalnum())


@app.get("/api/library")
def api_library():
    inv = store.get_library()
    prom = state["prom"]
    # what each node is serving right now
    loaded_by_node = {}
    for m in prom.get("models", {}).values():
        served = m.get("served_model")
        if m.get("up") and served:
            for h in m.get("hosts", []):
                loaded_by_node.setdefault(h, []).append(served)
    for node_id, o in state["ollama"].items():
        loaded_by_node.setdefault(node_id, []).extend(o.get("loaded") or [])

    nodes = {}
    for r in inv:
        served = loaded_by_node.get(r["node"], [])
        nr, loaded = _norm(r["model_id"]), False
        for s in served:
            ns = _norm(s)
            if ns and (ns in nr or nr in ns):
                loaded = True
        entry = nodes.setdefault(r["node"], {"id": r["node"], "models": [],
                                             "disk_bytes": 0})
        entry["models"].append({**r, "loaded": loaded})
        entry["disk_bytes"] += r.get("size_bytes") or 0
    labels = {n["id"]: n["label"] for n in cfg["nodes"]}
    out = [{**v, "label": labels.get(k, k)} for k, v in nodes.items()]
    order = {n["id"]: i for i, n in enumerate(cfg["nodes"])}
    out.sort(key=lambda n: order.get(n["id"], 99))
    return {"nodes": out}


@app.post("/api/library/tag")
def api_library_tag(payload: dict = Body(...)):
    store.set_tag(payload["node"], payload["model_id"],
                  payload.get("status") or "untested", payload.get("notes") or "")
    return {"ok": True}


@app.post("/api/library/inventory")
def api_library_inventory(payload: dict = Body(...)):
    rows = payload.get("models") or []
    store.replace_inventory(payload["node"], payload.get("source") or "hf-cache", rows)
    return {"ok": True, "count": len(rows)}


@app.get("/healthz")
def healthz():
    return {"ok": True}


static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
if os.path.isdir(static_dir):
    app.mount("/assets", StaticFiles(directory=os.path.join(static_dir, "assets")), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        full = os.path.join(static_dir, path)
        if path and os.path.isfile(full):
            return FileResponse(full)
        return FileResponse(os.path.join(static_dir, "index.html"))
