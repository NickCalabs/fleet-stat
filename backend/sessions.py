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


def _report_intervals(cfg, reports, now):
    """Turn harness pings into turn intervals per reported session.

    A `start` (prompt submitted) opens a turn, the next `stop` closes it; a bare
    `ping` covers ±120s. Claude-Code-style harnesses make many requests per turn
    (tool loops), so intervals beat nearest-timestamp matching here.
    """
    by_sess = {}
    for r in reports:
        by_sess.setdefault((r["harness"], r["session_id"]), []).append(r)
    canon = lambda m: (cfg["_models_by_name"].get(m) or {}).get("name", m) if m else None
    intervals, meta = [], {}
    turn_max = cfg["sessions"].get("report_turn_max_seconds", 3600)
    for key, pings in by_sess.items():
        pings.sort(key=lambda p: p["ts"])
        titled = [p for p in pings if p.get("title")]
        modeled = [p for p in pings if p.get("model")]
        meta[key] = {"title": titled[-1]["title"] if titled else None,
                     "model": canon(modeled[-1]["model"]) if modeled else None}
        for i, p in enumerate(pings):
            if p["event"] == "start":
                end = None
                for q in pings[i + 1:]:
                    end = q["ts"]
                    if q["event"] in ("stop", "start"):
                        break
                if end is None or end == p["ts"]:
                    end = min(p["ts"] + turn_max, now)
                intervals.append((p["ts"] - 15, end + 30, p["ts"], key))
            elif p["event"] == "ping":
                intervals.append((p["ts"] - 120, p["ts"] + 120, p["ts"], key))
    return intervals, meta


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

    # ── harness-reported sessions (POST /api/sessions/report) ──
    reporters = {h["id"]: {h["id"], *(h.get("match_harnesses") or [])}
                 for h in cfg.get("harnesses", []) if h.get("report_token")}
    reported = {}
    if reporters and loose:
        intervals, rmeta = _report_intervals(
            cfg, store.session_reports(since - 7200), now)
        still_loose = []
        for r in loose:
            best = None
            for lo, hi, t0, key in intervals:
                if not (lo <= r["start_ts"] <= hi):
                    continue
                if r["harness"] not in reporters.get(key[0], ()):
                    continue
                m = rmeta[key]["model"]
                if m and r["model_group"] and m != r["model_group"]:
                    continue
                if best is None or t0 > best[0]:   # latest turn start wins
                    best = (t0, key)
            if best:
                reported.setdefault(best[1], []).append(r)
            else:
                still_loose.append(r)
        loose = still_loose

    sessions = []
    for (harness, sid), rs in reported.items():
        sessions.append({
            "failed_requests": sum(1 for r in rs if r.get("status") == "failure"),
            "harness": harness,
            "model": max(rs, key=lambda r: r["start_ts"])["model_group"],
            "first_ts": min(r["start_ts"] for r in rs),
            "last_ts": max(r["end_ts"] or r["start_ts"] for r in rs),
            "requests": len(rs),
            "tokens_total": sum(r["total_tokens"] or 0 for r in rs),
            "ctx_tokens": max((r["prompt_tokens"] or 0) + (r["completion_tokens"] or 0)
                              for r in rs),
            "title": rmeta[(harness, sid)]["title"],
            "chat_id": f"{harness}:{sid}",
            "reported": True,
        })

    chat_by_id = {c["id"]: c for c in chats}
    utility_ids = {h["id"] for h in cfg.get("harnesses", []) if h.get("utility")}
    for cid, rs in assigned.items():
        c = chat_by_id[cid]
        sessions.append({
            "failed_requests": sum(1 for r in rs if r.get("status") == "failure"),
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
                    "failed_requests": 0,
                }
                gap_sessions.append(cur)
            cur["last_ts"] = r["end_ts"] or r["start_ts"]
            cur["requests"] += 1
            if r.get("status") == "failure":
                cur["failed_requests"] += 1
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
        fr = s.get("failed_requests") or 0
        out.append({
            **s,
            "harness_label": harness_labels.get(s["harness"], s["harness"]),
            "ctx_ceiling": ceiling,
            "fill_pct": fill,
            "active": (now - (s["last_ts"] or 0)) < active_s,
            "estimated": bool(s.get("estimated")),
            "reported": bool(s.get("reported")),
            "utility": s["harness"] in utility_ids,
            "failed": bool(s.get("requests")) and fr >= s["requests"],
        })
    out.sort(key=lambda s: s["last_ts"] or 0, reverse=True)
    return out
