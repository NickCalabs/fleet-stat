#!/usr/bin/env python3
"""oh-my-pi (omp) → Fleet Stat session sync.

omp writes every session to ~/.omp/agent/sessions/<project>/<ts>_<id>.jsonl,
including its title and, per assistant message, the provider `responseId` —
which is the same id LiteLLM logs as the request_id. This script tails those
files and reports {session_id, title, model, request_ids} to Fleet Stat, so
omp sessions show their real names with exact request→session links.

Run it as the omp user, every minute (cron):
    * * * * * python3 ~/.local/bin/fleet-stat-omp-sync.py >/dev/null 2>&1

Config: ~/.config/fleet-stat/report.env (chmod 600)
    FLEETSTAT_URL=http://10.0.0.5:8090
    FLEETSTAT_REPORT_TOKEN=<the omp harness's report_token in config.yaml>
stdlib only; state (file offsets) in ~/.config/fleet-stat/omp-sync.state.
"""
import glob
import json
import os
import time
import urllib.request

HOME = os.path.expanduser("~")
SESS_DIR = os.environ.get("OMP_SESSIONS", f"{HOME}/.omp/agent/sessions")
CONF_DIR = f"{HOME}/.config/fleet-stat"
STATE = f"{CONF_DIR}/omp-sync.state"
FIRST_RUN_DAYS = 7


def load_env():
    env = {}
    try:
        for line in open(f"{CONF_DIR}/report.env"):
            k, _, v = line.strip().partition("=")
            if k and not k.startswith("#"):
                env[k] = v.strip().strip('"')
    except OSError:
        pass
    return {**env, **{k: os.environ[k] for k in
                      ("FLEETSTAT_URL", "FLEETSTAT_REPORT_TOKEN") if k in os.environ}}


def scan(path, st):
    """Read new complete lines from `path` past st['offset']; update st."""
    ids, last_ts = [], None
    with open(path, "rb") as f:
        f.seek(st.get("offset", 0))
        data = f.read()
    end = data.rfind(b"\n") + 1          # never consume a half-written line
    for raw in data[:end].splitlines():
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        t = d.get("type")
        if t == "session":
            st["sid"] = d.get("id")
        elif t in ("title", "title_change") and d.get("title"):
            st["title"] = d["title"]
        elif t == "model_change" and d.get("model"):
            st["model"] = d["model"].split("/", 1)[-1]
        elif t == "message":
            m = d.get("message") or {}
            if m.get("role") == "assistant" and m.get("responseId"):
                ids.append(m["responseId"])
                st["model"] = m.get("model") or st.get("model")
                done = m.get("completedAt") or m.get("timestamp")
                if done:
                    last_ts = float(done) / 1000
    st["offset"] = st.get("offset", 0) + end
    return ids, last_ts


def main():
    env = load_env()
    url, token = env.get("FLEETSTAT_URL"), env.get("FLEETSTAT_REPORT_TOKEN")
    if not url or not token:
        return
    try:
        state = json.load(open(STATE))
    except (OSError, ValueError):
        state = {}
    cutoff = time.time() - FIRST_RUN_DAYS * 86400
    reports, new_state = [], dict(state)
    for path in glob.glob(f"{SESS_DIR}/*/*.jsonl"):
        try:
            size, mtime = os.path.getsize(path), os.path.getmtime(path)
        except OSError:
            continue
        st = dict(state.get(path) or {})
        if size <= st.get("offset", 0) or (not st and mtime < cutoff):
            continue
        prev_title = st.get("title")
        ids, last_ts = scan(path, st)
        if not st.get("sid"):
            st["sid"] = os.path.basename(path).rsplit("_", 1)[-1].removesuffix(".jsonl")
        new_state[path] = st
        if ids or st.get("title") != prev_title:
            reports.append({"session_id": st["sid"], "event": "ping",
                            "ts": last_ts or time.time(), "title": st.get("title"),
                            "model": st.get("model"), "request_ids": ids})
    for i in range(0, len(reports), 200):
        req = urllib.request.Request(
            url.rstrip("/") + "/api/sessions/report",
            data=json.dumps({"reports": reports[i:i + 200]}).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {token}"})
        urllib.request.urlopen(req, timeout=10).read()
    os.makedirs(CONF_DIR, exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(new_state, f)
    os.replace(tmp, STATE)   # only after the server accepted everything


if __name__ == "__main__":
    main()
