#!/usr/bin/env python3
"""Claude Code hook → Fleet Stat session report.

Through a LiteLLM proxy, Fleet Stat only sees model + key + source IP — never
the session's name. This hook tells it: on every prompt (start) and turn end
(stop) it POSTs {session_id, title, model} to /api/sessions/report.

Silent no-op unless FLEETSTAT_REPORT_TOKEN is set, so wire the env vars into
your local-fleet launcher only (cloud sessions then never report):

    FLEETSTAT_URL=http://10.0.0.5:8090
    FLEETSTAT_REPORT_TOKEN=<harness report_token from Fleet Stat config.yaml>

~/.claude/settings.json:
    "hooks": {
      "UserPromptSubmit": [{"hooks": [{"type": "command",
          "command": "python3 ~/.claude/hooks/fleet-stat-report.py"}]}],
      "Stop": [{"hooks": [{"type": "command",
          "command": "python3 ~/.claude/hooks/fleet-stat-report.py"}]}]
    }

Never blocks or fails the turn: 2s timeout, all errors swallowed, exit 0.
"""
import json
import os
import sys
import time
import urllib.request


def title_from_transcript(path):
    """Latest /rename title, else Claude Code's auto title."""
    custom = auto = None
    try:
        with open(os.path.expanduser(path)) as f:
            for line in f:
                if '"custom-title"' in line or '"ai-title"' in line:
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    if d.get("type") == "custom-title":
                        custom = d.get("customTitle") or custom
                    elif d.get("type") == "ai-title":
                        auto = d.get("aiTitle") or auto
    except OSError:
        pass
    return custom or auto


def main():
    token = os.environ.get("FLEETSTAT_REPORT_TOKEN")
    url = os.environ.get("FLEETSTAT_URL")
    if not token or not url:
        return
    hook = json.load(sys.stdin)
    event = {"UserPromptSubmit": "start", "Stop": "stop"}.get(
        hook.get("hook_event_name"), "ping")
    title = title_from_transcript(hook.get("transcript_path") or "")
    if not title and hook.get("prompt"):
        title = " ".join(hook["prompt"].split())[:60]
    body = {"session_id": hook.get("session_id"), "event": event, "ts": time.time(),
            "title": title, "model": os.environ.get("ANTHROPIC_MODEL")}
    req = urllib.request.Request(
        url.rstrip("/") + "/api/sessions/report", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    urllib.request.urlopen(req, timeout=2).read()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
