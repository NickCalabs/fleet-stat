#!/usr/bin/env python3
"""Fleet Stat log collector — tails engine logs on your nodes and pushes the
lines that matter to Fleet Stat's /api/events/ingest.

Runs OUTSIDE the container (cron, every minute) on a box that already has SSH
access to the nodes, so the dashboard never holds SSH keys. stdlib only.

    collect-logs.py /path/to/targets.json

targets.json:
{
  "server": "http://10.0.0.5:8090",
  "token": "optional — events.ingest_token from Fleet Stat's config.yaml",
  "window_seconds": 90,
  "targets": [
    {"node": "gpu-box", "ssh": "root@10.0.0.10", "unit": "vllm.service"},
    {"node": "spark-1", "ssh": "spark-1",        "docker": "vllm_node"},
    {"node": "gpu-box", "ssh": "root@10.0.0.10", "kernel": true},
    {"node": "hypervisor",                       "kernel": true}
  ],
  "ignore": ["Unknown vLLM environment variable", "unauthenticated requests to the HF Hub"]
}
"ignore" = regexes for lines you've judged harmless (dropped before sending).
A target without "ssh" runs locally. Each run looks back window_seconds
(> the cron interval); a <targets>.state file remembers what was already sent.
"""
import json
import re
import subprocess
import sys
import urllib.request
from datetime import datetime

# Lines worth surfacing. Everything else (access logs, per-10s throughput
# stats, debug chatter) is dropped — this is an event feed, not a log viewer.
KEEP = re.compile(
    r"ERROR|CRITICAL|WARNING|Traceback|Exception|out of memory|\bOOM\b|CUDA error|"
    r"NCCL (WARN|ERROR)|\bStarted\b|\bStopped\b|\bStarting\b|\bStopping\b|"
    r"Application startup complete|Shutting down|Loading weights took|"
    r"Model loading took|Xid|oom-kill|Killed process|segfault|hung_task|"
    r"Failed with result|Main process exited", re.I)
DROP = re.compile(r'" 200\b|GET /metrics|GET /health|loggers\.py.*throughput|UFW BLOCK', re.I)
KERNEL_KEEP = re.compile(r"Out of memory|oom-kill|Killed process|NVRM: Xid|segfault|"
                         r"hung_task|blocked for more than|I/O error|EXT4-fs error|"
                         r"Hardware Error|mce:", re.I)
ERROR = re.compile(r"ERROR|CRITICAL|Traceback|out of memory|\bOOM\b|CUDA error|Xid|"
                   r"oom-kill|Killed process|segfault|Failed with result|"
                   r"I/O error|Hardware Error", re.I)
WARN = re.compile(r"WARNING|WARN\b|hung_task|blocked for more than", re.I)
MAX_PER_TARGET = 60
# vLLM prefixes every line with "(Worker_TP0 pid=253) WARNING 10-02 05:10:54
# [file.py:135]" — strip it; the level is kept separately.
VLLM_PREFIX = re.compile(r"^(\(\w+ pid=\d+\)\s*)?((INFO|WARNING|ERROR|DEBUG|CRITICAL)"
                         r"\s+\d\d-\d\d \d\d:\d\d:\d\d\s+\[([^\]:]+)[^\]]*\]\s*)?")


def clean(msg):
    m = VLLM_PREFIX.match(msg)
    if m and m.group(2):
        where = m.group(4).removesuffix(".py")
        return f"{where}: {msg[m.end():]}"
    msg = msg[m.end():] if m and m.group(1) else msg
    return re.sub(r"^(INFO|WARNING|ERROR):\s+", "", msg)   # uvicorn prefix


def run(target, cmd):
    if target.get("ssh"):
        cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6",
               target["ssh"], " ".join(cmd)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=40)
        return out.stdout + out.stderr if target.get("docker") else out.stdout
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"FAIL {target.get('node')}: {e}", file=sys.stderr)
        return ""


def parse_journal(text):
    for line in text.splitlines():
        # short-unix: "<epoch> <host> <ident>[pid]: msg"
        parts = line.split(" ", 2)
        if len(parts) < 3:
            continue
        try:
            ts = float(parts[0])
        except ValueError:
            continue
        msg = parts[2].split(": ", 1)[-1]
        yield ts, msg


def parse_docker(text):
    for line in text.splitlines():
        stamp, _, msg = line.partition(" ")
        try:
            ts = datetime.fromisoformat(re.sub(r"\.(\d{6})\d*", r".\1",
                                               stamp.replace("Z", "+00:00"))).timestamp()
        except ValueError:
            continue
        yield ts, msg


def level_of(msg):
    if ERROR.search(msg):
        return "error"
    if WARN.search(msg):
        return "warn"
    return "info"


def collect(target, window, ignore=None, after=0):
    node = target["node"]
    since = f"-{window}s"
    if target.get("kernel"):
        lines = parse_journal(run(target, ["journalctl", "-k", "--since", f'"{since}"',
                                           "--no-pager", "-q", "-o", "short-unix"]))
        keep, source = KERNEL_KEEP, "kernel"
    elif target.get("unit"):
        lines = parse_journal(run(target, ["journalctl", "-u", target["unit"], "--since",
                                           f'"{since}"', "--no-pager", "-q",
                                           "-o", "short-unix"]))
        keep, source = KEEP, target["unit"].removesuffix(".service")
    elif target.get("docker"):
        lines = parse_docker(run(target, ["docker", "logs", "--since", f"{window}s",
                                          "--timestamps", target["docker"]]))
        keep, source = KEEP, target["docker"]
    else:
        return []
    events, seen = [], {}
    for ts, raw in lines:
        if ts <= after:      # already sent on a previous run
            continue
        raw = raw.strip()
        if not raw or DROP.search(raw) or not keep.search(raw):
            continue
        if ignore and ignore.search(raw):
            continue
        msg = clean(raw)
        # collapse burst repeats (TP workers log the same line N times at once)
        sig = re.sub(r"\d+", "#", msg)
        if sig in seen and ts - seen[sig]["ts"] <= 5:
            seen[sig]["n"] += 1
            continue
        e = {"ts": ts, "node": node, "source": source, "kind": "log",
             "level": level_of(raw), "title": msg[:300],
             "detail": raw if len(raw) > 300 or raw != msg else None, "n": 1}
        seen[sig] = e
        events.append(e)
    for e in events:
        n = e.pop("n")
        if n > 1:
            e["title"] = e["title"][:290] + f"  (×{n})"
    return events[-MAX_PER_TARGET:]


def main():
    cfg = json.load(open(sys.argv[1]))
    window = int(cfg.get("window_seconds", 90))
    events = []
    ignore = re.compile("|".join(cfg["ignore"]), re.I) if cfg.get("ignore") else None
    # per-target high-water mark, so overlapping windows never double-send
    state_path = sys.argv[1] + ".state"
    try:
        state = json.load(open(state_path))
    except (OSError, ValueError):
        state = {}
    for t in cfg["targets"]:
        key = json.dumps(t, sort_keys=True)
        got = collect(t, window, ignore, state.get(key, 0))
        if got:
            state[key] = max(e["ts"] for e in got)
        events.extend(got)
    if not events:
        return
    req = urllib.request.Request(
        cfg["server"].rstrip("/") + "/api/events/ingest",
        data=json.dumps({"events": events}).encode(),
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {cfg['token']}"} if cfg.get("token") else {})})
    with urllib.request.urlopen(req, timeout=15) as r:
        print(r.read().decode())
    json.dump(state, open(state_path, "w"))   # only after the server took them


if __name__ == "__main__":
    main()
