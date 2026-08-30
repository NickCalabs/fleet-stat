import asyncio
import json
import logging
import os
import shutil
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import httpx

from .config import resolve_harness

log = logging.getLogger("fleet-stat")

# Shared in-memory snapshot the API serves from. Pollers replace whole sub-dicts
# atomically so readers never see a half-written snapshot.
state = {
    "prom": {"ts": 0, "nodes": {}, "models": {}, "targets": {}, "error": None},
    "owui": {"ts": 0, "chats": [], "users": {}, "error": None},
    "litellm": {"ts": 0, "up": False, "keys": {}, "error": None},
    "ollama": {},  # node_id -> {"loaded": [names], "ts": ...}
    "comfy": {},   # model name -> {"up", "running", "pending", "vram_*", ...}
}

PROM_QUERIES = {
    "up": "up",
    "cpu_pct": '100 * (1 - avg by(instance) (rate(node_cpu_seconds_total{mode="idle"}[2m])))',
    "mem_pct": "100 * (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)",
    "load1": "node_load1",
    "uptime_s": "time() - node_boot_time_seconds",
    "cpu_temp": "max by(instance) (node_hwmon_temp_celsius)",
    "gpu_util": "nvidia_smi_utilization_gpu_ratio",
    "gpu_mem_used": "nvidia_smi_memory_used_bytes",
    "gpu_mem_total": "nvidia_smi_memory_total_bytes",
    "gpu_temp": "nvidia_smi_temperature_gpu",
    "gpu_power": "nvidia_smi_power_draw_watts",
    "vllm_running": "vllm:num_requests_running",
    "vllm_waiting": "vllm:num_requests_waiting",
    "vllm_kv_cache": "vllm:kv_cache_usage_perc or vllm:gpu_cache_usage_perc",
    "vllm_prompt_tps": "rate(vllm:prompt_tokens_total[2m])",
    "vllm_gen_tps": "rate(vllm:generation_tokens_total[2m])",
    "ollama_up": "ollama_up",
    "ollama_loaded": "ollama_models_loaded",
    "ollama_vram": "ollama_vram_used_bytes",
}


async def _prom_query(client, base, query):
    r = await client.get(f"{base}/api/v1/query", params={"query": query}, timeout=8)
    r.raise_for_status()
    out = []
    for res in r.json()["data"]["result"]:
        try:
            out.append((res["metric"], float(res["value"][1])))
        except (ValueError, KeyError):
            continue
    return out


def _by_instance(rows):
    return {m.get("instance"): v for m, v in rows}


async def poll_prometheus(cfg):
    base = cfg["sources"]["prometheus"]["url"]
    interval = cfg["poll"]["prometheus_seconds"]
    async with httpx.AsyncClient() as client:
        while True:
            try:
                results = await asyncio.gather(
                    *[_prom_query(client, base, q) for q in PROM_QUERIES.values()]
                )
                data = dict(zip(PROM_QUERIES.keys(), results))
                snap = _assemble_prom(cfg, data)
                snap["ts"] = time.time()
                snap["error"] = None
                state["prom"] = snap
            except Exception as e:
                state["prom"] = {**state["prom"], "error": str(e)}
                log.warning("prometheus poll failed: %s", e)
            await asyncio.sleep(interval)


def _assemble_prom(cfg, data):
    up = {m.get("instance"): v for m, v in data["up"]}
    cpu = _by_instance(data["cpu_pct"])
    mem = _by_instance(data["mem_pct"])
    load1 = _by_instance(data["load1"])
    uptime = _by_instance(data["uptime_s"])
    ctemp = _by_instance(data["cpu_temp"])
    g_util = _by_instance(data["gpu_util"])
    g_used = _by_instance(data["gpu_mem_used"])
    g_total = _by_instance(data["gpu_mem_total"])
    g_temp = _by_instance(data["gpu_temp"])
    g_power = _by_instance(data["gpu_power"])

    nodes = {}
    for n in cfg["nodes"]:
        ni = n.get("node_instance")
        gi = n.get("gpu_instance")
        node = {
            "id": n["id"], "label": n["label"], "hw": n.get("hw", ""),
            "cluster": n.get("cluster"),
        }
        if ni:
            node["up"] = up.get(ni, 0) == 1
            if node["up"]:
                node["cpu_pct"] = cpu.get(ni)
                node["mem_pct"] = mem.get(ni)
                node["load1"] = load1.get(ni)
                node["uptime_s"] = uptime.get(ni)
                node["cpu_temp"] = ctemp.get(ni)
        elif n.get("ollama"):
            o_up = data["ollama_up"]
            node["up"] = bool(o_up) and o_up[0][1] == 1
            node["ollama_loaded"] = data["ollama_loaded"][0][1] if data["ollama_loaded"] else None
        if gi and up.get(gi, 0) == 1:
            node["gpu"] = {
                "util_pct": (g_util.get(gi) or 0) * 100,
                "vram_used": g_used.get(gi),
                "vram_total": g_total.get(gi),
                "temp": g_temp.get(gi),
                "power_w": g_power.get(gi),
            }
        nodes[n["id"]] = node

    v_running = {m.get("instance"): (m, v) for m, v in data["vllm_running"]}
    v_waiting = _by_instance(data["vllm_waiting"])
    v_kv = _by_instance(data["vllm_kv_cache"])
    v_ptps = _by_instance(data["vllm_prompt_tps"])
    v_gtps = _by_instance(data["vllm_gen_tps"])

    models = {}
    for m in cfg["models"]:
        entry = {
            "name": m["name"], "hosts": m.get("hosts", []),
            "cluster": bool(m.get("cluster")), "engine": m.get("engine"),
            "ctx": m.get("ctx"), "max_output": m.get("max_output"),
            "hidden": bool(m.get("hidden")), "aliases": m.get("aliases") or [],
        }
        vi = m.get("vllm_instance")
        if m.get("engine") == "vllm" and vi:
            entry["up"] = up.get(vi, 0) == 1 and vi in v_running
            if vi in v_running:
                metric, running = v_running[vi]
                entry["served_model"] = metric.get("model_name")
                entry["running"] = running
                entry["waiting"] = v_waiting.get(vi, 0)
                entry["kv_cache_pct"] = (v_kv.get(vi) or 0) * 100
                entry["prompt_tps"] = v_ptps.get(vi, 0)
                entry["gen_tps"] = v_gtps.get(vi, 0)
        elif m.get("engine") == "ollama":
            o_up = data["ollama_up"]
            entry["up"] = bool(o_up) and o_up[0][1] == 1
        models[m["name"]] = entry

    return {"nodes": nodes, "models": models, "targets": up}


def _parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _normalize_spend_row(cfg, row, keys):
    md = row.get("metadata") or {}
    api_key = row.get("api_key") or ""
    alias = md.get("user_api_key_alias") or keys.get(api_key)
    ip = md.get("requester_ip_address")
    raw_group = row.get("model_group") or row.get("model")
    known = cfg["_models_by_name"].get(raw_group)
    model_group = known["name"] if known else raw_group
    return {
        "request_id": row.get("request_id"),
        "start_ts": _parse_iso(row.get("startTime")),
        "end_ts": _parse_iso(row.get("endTime")),
        "completion_start_ts": _parse_iso(row.get("completionStartTime")),
        "model_group": model_group,
        "model": row.get("model"),
        "api_base": row.get("api_base"),
        "key_hash": api_key,
        "key_alias": alias,
        "harness": resolve_harness(cfg, alias, ip, api_key),
        "user_id": row.get("user"),
        "requester_ip": ip,
        "prompt_tokens": row.get("prompt_tokens") or 0,
        "completion_tokens": row.get("completion_tokens") or 0,
        "total_tokens": row.get("total_tokens") or 0,
        "spend": row.get("spend") or 0.0,
        "call_type": row.get("call_type"),
        "status": md.get("status") or ("error" if md.get("error_information") else "success"),
    }


async def poll_litellm(cfg, store):
    src = cfg["sources"]["litellm"]
    base, headers = src["url"], {"Authorization": f"Bearer {src['master_key']}"}
    interval = cfg["poll"]["litellm_seconds"]
    keys_ts = 0
    async with httpx.AsyncClient(headers=headers) as client:
        while True:
            try:
                # key hash -> alias map, refreshed every 5 min
                if time.time() - keys_ts > 300:
                    r = await client.get(f"{base}/key/list",
                                         params={"return_full_object": "true", "size": 100},
                                         timeout=10)
                    r.raise_for_status()
                    keys = {}
                    for k in r.json().get("keys", []):
                        if isinstance(k, dict) and k.get("token"):
                            keys[k["token"]] = k.get("key_alias")
                    state["litellm"] = {**state["litellm"], "keys": keys}
                    keys_ts = time.time()

                cursor = float(store.get_meta("litellm_cursor", "0") or 0)
                # first run: backfill 30 days; then re-fetch a 10-min overlap window
                start = max(cursor - 600, time.time() - 30 * 86400)
                fmt = "%Y-%m-%d %H:%M:%S"
                start_s = datetime.fromtimestamp(start, tz=timezone.utc).strftime(fmt)
                end_s = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime(fmt)

                page, max_seen, fetched = 1, cursor, 0
                keys = state["litellm"].get("keys", {})
                while page <= 40:
                    r = await client.get(
                        f"{base}/spend/logs/ui",
                        params={"start_date": start_s, "end_date": end_s,
                                "page": page, "page_size": 500},
                        timeout=30)
                    r.raise_for_status()
                    body = r.json()
                    rows = body.get("data") or []
                    norm = [_normalize_spend_row(cfg, x, keys) for x in rows]
                    norm = [x for x in norm if x["request_id"] and x["start_ts"]
                                and (x["model_group"] or x["total_tokens"])]
                    store.upsert_requests(norm)
                    fetched += len(norm)
                    for x in norm:
                        max_seen = max(max_seen, x["start_ts"])
                    total_pages = body.get("total_pages") or 1
                    if page >= total_pages or not rows:
                        break
                    page += 1

                if max_seen > cursor:
                    store.set_meta("litellm_cursor", max_seen)
                state["litellm"] = {**state["litellm"], "ts": time.time(),
                                    "up": True, "error": None, "last_fetch": fetched}
            except Exception as e:
                state["litellm"] = {**state["litellm"], "up": False, "error": str(e)}
                log.warning("litellm poll failed: %s", e)
            await asyncio.sleep(interval)


def _read_owui(db_path):
    """Copy the live SQLite (plus WAL sidecars) and read the copy — avoids
    read-only-mount WAL locking issues entirely."""
    tmp_dir = "/tmp/owui-copy"
    os.makedirs(tmp_dir, exist_ok=True)
    dst = os.path.join(tmp_dir, "webui.db")
    shutil.copy2(db_path, dst)
    for ext in ("-wal", "-shm"):
        src = db_path + ext
        if os.path.exists(src):
            shutil.copy2(src, dst + ext)
        elif os.path.exists(dst + ext):
            os.remove(dst + ext)

    con = sqlite3.connect(dst)
    con.row_factory = sqlite3.Row
    since = time.time() - 7 * 86400  # keep a week of chats for title enrichment
    chats = []
    for r in con.execute(
        """SELECT c.id, c.title, c.user_id, c.updated_at,
                  json_extract(c.chat, '$.models') AS models
           FROM chat c WHERE c.updated_at > ? AND c.archived = 0
           ORDER BY c.updated_at DESC LIMIT 250""", (since,)):
        chats.append(dict(r))

    if chats:
        ids = [c["id"] for c in chats]
        ph = ",".join("?" for _ in ids)
        stats = {r["chat_id"]: dict(r) for r in con.execute(
            f"""SELECT chat_id, COUNT(*) AS n_msgs,
                       SUM(LENGTH(COALESCE(content,''))) AS chars,
                       MIN(updated_at) AS first_msg_at,
                       MAX(updated_at) AS last_msg_at,
                       MAX(CASE WHEN role='assistant' THEN model_id END) AS last_model
                FROM chat_message WHERE chat_id IN ({ph}) GROUP BY chat_id""", ids)}
        msg_ts = {}
        for r in con.execute(
            f"""SELECT chat_id, updated_at FROM chat_message
                WHERE role='assistant' AND chat_id IN ({ph}) AND updated_at > ?""",
            ids + [since]):
            msg_ts.setdefault(r["chat_id"], []).append(r["updated_at"])
        for c in chats:
            c.update(stats.get(c["id"], {}))
            c["msg_ts"] = sorted(msg_ts.get(c["id"], []))
            try:
                c["models"] = json.loads(c["models"]) if c.get("models") else []
            except (TypeError, ValueError):
                c["models"] = []

    users = {}
    try:
        users = {r["id"]: r["name"] for r in con.execute("SELECT id, name FROM user")}
    except sqlite3.Error:
        pass
    con.close()
    return chats, users


async def poll_owui(cfg):
    db_path = cfg["sources"]["openwebui"]["db_path"]
    state["owui"]["enabled"] = True
    interval = cfg["poll"]["owui_seconds"]
    while True:
        try:
            chats, users = await asyncio.to_thread(_read_owui, db_path)
            state["owui"] = {"ts": time.time(), "chats": chats, "users": users,
                            "error": None, "enabled": True}
        except Exception as e:
            state["owui"] = {**state["owui"], "error": str(e)}
            log.warning("owui poll failed: %s", e)
        await asyncio.sleep(interval)


def _parse_ollama_ts(s):
    if not s:
        return None
    import re
    s = re.sub(r"\.(\d{1,6})\d*", r".\1", s)  # ollama uses ns precision; trim to µs
    try:
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


async def poll_ollama_nodes(cfg, store):
    """On-disk + loaded models from nodes serving via ollama (HTTP API, no SSH)."""
    targets = [(n["id"], n["ollama_url"].rstrip("/"))
               for n in cfg["nodes"] if n.get("ollama_url")]
    if not targets:
        return
    async with httpx.AsyncClient() as client:
        while True:
            for node_id, base in targets:
                try:
                    tags = (await client.get(f"{base}/api/tags", timeout=8)).json()
                    ps = (await client.get(f"{base}/api/ps", timeout=8)).json()
                    rows = []
                    for m in tags.get("models", []):
                        d = m.get("details") or {}
                        rows.append({
                            "id": m["name"], "path": None,
                            "size_bytes": m.get("size"),
                            "mtime": _parse_ollama_ts(m.get("modified_at")),
                            "quant": d.get("quantization_level"),
                            "params": d.get("parameter_size"),
                        })
                    store.replace_inventory(node_id, "ollama", rows)
                    state["ollama"][node_id] = {
                        "loaded": [m["name"] for m in ps.get("models", [])],
                        "ts": time.time(),
                    }
                except Exception as e:
                    state["ollama"][node_id] = {**state["ollama"].get(node_id, {}),
                                                "error": str(e)}
                    log.warning("ollama poll %s failed: %s", node_id, e)
            await asyncio.sleep(60)


def _comfy_current_model(queue_running):
    try:
        prompt = queue_running[0][2]
        for node in prompt.values():
            for k in ("ckpt_name", "unet_name", "model"):
                v = (node.get("inputs") or {}).get(k)
                if isinstance(v, str) and v:
                    return v
    except (IndexError, KeyError, AttributeError, TypeError):
        pass
    return None


async def poll_comfy(cfg):
    targets = [(m["name"], m["comfy_url"].rstrip("/"))
               for m in cfg["models"] if m.get("engine") == "comfyui" and m.get("comfy_url")]
    if not targets:
        return
    async with httpx.AsyncClient() as client:
        while True:
            for name, base in targets:
                try:
                    stats = (await client.get(f"{base}/system_stats", timeout=6)).json()
                    q = (await client.get(f"{base}/queue", timeout=6)).json()
                    dev = (stats.get("devices") or [{}])[0]
                    state["comfy"][name] = {
                        "up": True, "ts": time.time(),
                        "running": len(q.get("queue_running") or []),
                        "pending": len(q.get("queue_pending") or []),
                        "vram_total": dev.get("vram_total"),
                        "vram_free": dev.get("vram_free"),
                        "version": (stats.get("system") or {}).get("comfyui_version"),
                        "current_model": _comfy_current_model(q.get("queue_running") or []),
                    }
                except Exception as e:
                    state["comfy"][name] = {"up": False, "ts": time.time(), "error": str(e)}
            await asyncio.sleep(20)


def start_pollers(cfg, store):
    tasks = [
        asyncio.create_task(poll_prometheus(cfg)),
        asyncio.create_task(poll_litellm(cfg, store)),
        asyncio.create_task(poll_ollama_nodes(cfg, store)),
        asyncio.create_task(poll_comfy(cfg)),
    ]
    if (cfg["sources"].get("openwebui") or {}).get("db_path"):
        tasks.append(asyncio.create_task(poll_owui(cfg)))
    return tasks
