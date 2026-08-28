import time
from bisect import bisect_left

from .config import model_ctx
from .pollers import state


def _nearest(sorted_ts, t):
    if not sorted_ts:
        return float("inf")
    i = bisect_left(sorted_ts, t)
    c1 = abs(sorted_ts[i] - t) if i < len(sorted_ts) else float("inf")
    c0 = abs(sorted_ts[i - 1] - t) if i > 0 else float("inf")
    return min(c0, c1)


def build_sessions(cfg, store, window_hours):
    """One row per conversation.

    Open WebUI requests are assigned to their chat individually — a spend-log
    request finishes within seconds of its assistant message hitting the chat
    DB, so per-request nearest-message matching (±120s) is near-exact. This is
    the unit users think in; time-gap sessions crossed wires whenever two chats
    ran back-to-back on the same model. Other harnesses (no chat DB) still use
    gap-based sessions.
    """
    now = time.time()
    gap = cfg["sessions"]["gap_seconds"]
    active_s = cfg["sessions"]["active_seconds"]
    owui_id = (cfg["sources"].get("openwebui") or {}).get("harness_id", "openwebui")
    since = now - window_hours * 3600

    rows = store.recent_requests(since)
    owui = state["owui"]
    chats = list(owui.get("chats") or [])
    users = owui.get("users") or {}
    harness_labels = {h["id"]: h.get("label", h["id"]) for h in cfg.get("harnesses", [])}

    # ── per-request chat assignment for the OWUI harness ──
    assigned = {}          # chat_id -> [request rows]
    loose = []             # requests for gap-based sessionization
    for r in rows:
        if r["harness"] != owui_id or not chats:
            loose.append(r)
            continue
        rt = r["end_ts"] or r["start_ts"]
        best = None
        for c in chats:
            d = _nearest(c.get("msg_ts") or [], rt)
            if d <= 120 and (best is None or d < best[0]):
                best = (d, c["id"])
        if best:
            assigned.setdefault(best[1], []).append(r)
        else:
            loose.append(r)

    sessions = []
    chat_by_id = {c["id"]: c for c in chats}
    for cid, rs in assigned.items():
        c = chat_by_id[cid]
        sessions.append({
            "harness": owui_id,
            "model": max(rs, key=lambda r: r["start_ts"])["model_group"],
            "first_ts": min(r["start_ts"] for r in rs),
            "last_ts": max(r["end_ts"] or r["start_ts"] for r in rs),
            "requests": len(rs),
            "tokens_total": sum(r["total_tokens"] or 0 for r in rs),
            "ctx_tokens": max((r["prompt_tokens"] or 0) + (r["completion_tokens"] or 0)
                              for r in rs),
            "title": c.get("title"),
            "chat_id": cid,
            "user": users.get(c.get("user_id")),
        })

    # ── gap-based sessions for everything else ──
    groups = {}
    for r in loose:
        groups.setdefault((r["harness"], r["model_group"]), []).append(r)
    gap_sessions = []
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
                gap_sessions.append(cur)
            cur["last_ts"] = r["end_ts"] or r["start_ts"]
            cur["requests"] += 1
            cur["tokens_total"] += r["total_tokens"] or 0
            cur["ctx_tokens"] = max(
                cur["ctx_tokens"],
                (r["prompt_tokens"] or 0) + (r["completion_tokens"] or 0),
            )
    sessions.extend(gap_sessions)

    # ── OWUI chats with no spend rows (predate spend logging) -> estimated ──
    def overlaps_gap_session(c_ts):
        return any(s["harness"] == owui_id and
                   s["first_ts"] - 600 <= c_ts <= s["last_ts"] + 600
                   for s in gap_sessions)

    for c in chats:
        c_ts = c.get("last_msg_at") or c.get("updated_at") or 0
        if c["id"] in assigned or c_ts < since or overlaps_gap_session(c_ts):
            continue
        model = next(iter(c.get("models") or []), None)
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
