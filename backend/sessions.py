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
                    "_req_ts": [],
                }
                sessions.append(cur)
            cur["last_ts"] = r["end_ts"] or r["start_ts"]
            cur["_req_ts"].append(cur["last_ts"])
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

    # Per-request attribution: a spend-log request finishes within seconds of
    # its assistant message being written to the chat DB. Count, per chat, how
    # many of the session's requests land within ±120s of one of that chat's
    # message timestamps — far more precise than span overlap, which crossed
    # wires when two chats on the same model were active in the same period.
    from bisect import bisect_left

    def nearest(sorted_ts, t):
        if not sorted_ts:
            return float("inf")
        i = bisect_left(sorted_ts, t)
        c1 = abs(sorted_ts[i] - t) if i < len(sorted_ts) else float("inf")
        c0 = abs(sorted_ts[i - 1] - t) if i > 0 else float("inf")
        return min(c0, c1)

    matched_chats = set()
    owui_sessions = [s for s in sessions if s["harness"] == owui_id]
    for s in owui_sessions:
        best = None
        for c in chats:
            ts_list = c.get("msg_ts") or []
            hits, near = 0, float("inf")
            for rt in s.get("_req_ts") or []:
                d = nearest(ts_list, rt)
                if d <= 120:
                    hits += 1
                near = min(near, d)
            if not hits:
                continue
            score = (hits, -near)
            if best is None or score > best[0]:
                best = (score, c)
        if best:
            _, c = best
            matched_chats.add(c["id"])
            s["title"] = c.get("title")
            s["chat_id"] = c["id"]
            s["user"] = users.get(c.get("user_id"))

    # Merge gap-split sessions of the same chat into one row per conversation.
    by_chat = {}
    merged_out = []
    for s in sessions:
        cid = s.get("chat_id")
        if s["harness"] == owui_id and cid:
            if cid in by_chat:
                t = by_chat[cid]
                t["first_ts"] = min(t["first_ts"], s["first_ts"])
                t["last_ts"] = max(t["last_ts"], s["last_ts"])
                t["requests"] += s["requests"]
                t["tokens_total"] += s["tokens_total"]
                t["ctx_tokens"] = max(t["ctx_tokens"], s["ctx_tokens"])
                continue
            by_chat[cid] = s
        merged_out.append(s)
    sessions = merged_out

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
        s.pop("_req_ts", None)
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
