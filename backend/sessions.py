import time

from .config import model_ctx
from .pollers import state


def build_sessions(cfg, store, window_hours):
    """Fuse LiteLLM request rows into per-harness sessions, then enrich
    Open WebUI sessions with chat titles (time+model correlation) and add
    OWUI chats that produced no spend rows as estimated sessions."""
    now = time.time()
    gap = cfg["sessions"]["gap_seconds"]
    active_s = cfg["sessions"]["active_seconds"]
    owui_id = (cfg["sources"].get("openwebui") or {}).get("harness_id", "openwebui")
    since = now - window_hours * 3600

    rows = store.recent_requests(since)
    groups = {}
    for r in rows:
        groups.setdefault((r["harness"], r["model_group"]), []).append(r)

    sessions = []
    for (harness, model_group), rs in groups.items():
        rs.sort(key=lambda r: r["start_ts"])
        cur = None
        for r in rs:
            if cur is None or r["start_ts"] - cur["last_ts"] > gap:
                cur = {
                    "harness": harness, "model": model_group,
                    "first_ts": r["start_ts"], "last_ts": r["start_ts"],
                    "requests": 0, "tokens_total": 0, "ctx_tokens": 0,
                }
                sessions.append(cur)
            cur["last_ts"] = r["end_ts"] or r["start_ts"]
            cur["requests"] += 1
            cur["tokens_total"] += r["total_tokens"] or 0
            # Current context = the session's LARGEST prompt+completion, not the
            # newest: OWUI fires small title/tag/follow-up task calls after each
            # turn, so the newest request is usually a ~500-token aux call.
            # Real conversation context grows monotonically, so max is right.
            cur["ctx_tokens"] = max(
                cur["ctx_tokens"],
                (r["prompt_tokens"] or 0) + (r["completion_tokens"] or 0),
            )

    owui = state["owui"]
    chats = list(owui.get("chats") or [])
    users = owui.get("users") or {}
    harness_labels = {h["id"]: h.get("label", h["id"]) for h in cfg.get("harnesses", [])}

    # Attach OWUI chat titles to openwebui sessions by time+model proximity.
    def canon(name):
        m = cfg["_models_by_name"].get(name)
        return m["name"] if m else name

    matched_chats = set()
    owui_sessions = [s for s in sessions if s["harness"] == owui_id]
    for s in owui_sessions:
        best = None
        for c in chats:
            if c["id"] in matched_chats:
                continue
            c_ts = c.get("last_msg_at") or c.get("updated_at") or 0
            if not (s["first_ts"] - 600 <= c_ts <= s["last_ts"] + 600):
                continue
            c_models = [canon(m) for m in (c.get("models") or [])]
            if c_models and canon(s["model"]) not in c_models:
                continue
            d = abs(c_ts - s["last_ts"])
            if best is None or d < best[0]:
                best = (d, c)
        if best:
            _, c = best
            matched_chats.add(c["id"])
            s["title"] = c.get("title")
            s["chat_id"] = c["id"]
            s["user"] = users.get(c.get("user_id"))

    # OWUI chats active in the window that matched no spend rows -> estimated.
    # Skip chats whose activity overlaps any spend-backed OWUI session: that
    # traffic is already represented, only the title-match failed.
    def overlaps_session(c_ts):
        return any(s["first_ts"] - 600 <= c_ts <= s["last_ts"] + 600 for s in owui_sessions)

    for c in chats:
        c_ts = c.get("last_msg_at") or c.get("updated_at") or 0
        if c["id"] in matched_chats or c_ts < since or overlaps_session(c_ts):
            continue
        model = None
        for m in c.get("models") or []:
            model = m
            break
        est_tokens = int((c.get("chars") or 0) / 4)
        sessions.append({
            "harness": owui_id, "model": model or c.get("last_model"),
            "first_ts": c.get("updated_at"), "last_ts": c_ts,
            "requests": c.get("n_msgs") or 0, "tokens_total": est_tokens,
            "ctx_tokens": est_tokens, "estimated": True,
            "title": c.get("title"), "chat_id": c["id"],
            "user": users.get(c.get("user_id")),
        })

    out = []
    for s in sessions:
        ceiling = model_ctx(cfg, s["model"]) if s["model"] else None
        fill = round(100.0 * s["ctx_tokens"] / ceiling, 1) if ceiling else None
        out.append({
            **s,
            "harness_label": harness_labels.get(s["harness"], s["harness"]),
            "ctx_ceiling": ceiling,
            "fill_pct": fill,
            "active": (now - (s["last_ts"] or 0)) < active_s,
            "estimated": bool(s.get("estimated")),
        })
    out.sort(key=lambda s: s["last_ts"] or 0, reverse=True)
    return out
