#!/usr/bin/env python3
# <bitbar.title>Fleet Stat</bitbar.title>
# <bitbar.version>v0.1</bitbar.version>
# <bitbar.desc>Menubar glance for a Fleet Stat server (self-hosted LLM fleet).</bitbar.desc>
# <bitbar.dependencies>python3</bitbar.dependencies>
# <bitbar.abouturl>https://github.com/NickCalabs/fleet-stat</bitbar.abouturl>
#
# Setup: put your server URL in ~/.config/fleet-stat/server, e.g.
#   mkdir -p ~/.config/fleet-stat && echo "http://10.0.0.20:8090" > ~/.config/fleet-stat/server
# Refresh interval is the filename (30s). Rename to taste.

import json
import os
import time
import urllib.request

GOOD, WARN, CRIT, MUTED, BLUE = "#0ca30c", "#fab219", "#d03b3b", "#898781", "#3987e5"


def base_url():
    try:
        with open(os.path.expanduser("~/.config/fleet-stat/server")) as f:
            u = f.read().strip()
            if u:
                return u.rstrip("/")
    except OSError:
        pass
    return os.environ.get("FLEET_STAT_URL", "http://localhost:8090").rstrip("/")


BASE = base_url()


def get(path):
    req = urllib.request.Request(BASE + path, headers={"User-Agent": "fleet-stat-swiftbar"})
    with urllib.request.urlopen(req, timeout=4) as r:
        return json.load(r)


def fmt_n(n):
    if n is None:
        return "—"
    if n >= 1e6:
        return f"{n / 1e6:.1f}M"
    if n >= 1e3:
        return f"{n / 1e3:.0f}k"
    return str(int(n))


def ago(ts):
    s = max(0, time.time() - (ts or 0))
    if s < 60:
        return f"{s:.0f}s"
    if s < 3600:
        return f"{s / 60:.0f}m"
    return f"{s / 3600:.1f}h"


def trim(s, n=36):
    s = s or ""
    return s if len(s) <= n else s[: n - 1] + "…"


try:
    fleet = get("/api/fleet")
    sessions = get("/api/sessions?hours=72")["sessions"]
except Exception:
    print(f"⬢ ⋯ | color={MUTED}")
    print("---")
    print(f"Fleet Stat unreachable | color={CRIT}")
    print(f"{BASE} | color={MUTED} size=11")
    print("On the road? Check WireGuard. | size=11")
    print("Retry | refresh=true")
    raise SystemExit(0)

models = [m for m in fleet["models"] if not m.get("hidden")]
up = [m for m in models if m.get("up")]
tps = sum(m.get("gen_tps") or 0 for m in up)
active = [s for s in sessions if s.get("active")]

# ── menubar title ──
if len(up) < len(models):
    print(f"⬢ {len(up)}/{len(models)} | color={CRIT}")
elif tps >= 1:
    print(f"⬢ {tps:.0f}t/s | color={GOOD}")
else:
    print(f"⬢ {len(up)}/{len(models)}")

# ── dropdown ──
print("---")
print(f"Fleet Stat — {len(up)}/{len(models)} models · {len(active)} active | href={BASE}/")
print("---")
for m in models:
    if not m.get("up"):
        print(f"○ {m['name']} — down | color={CRIT}")
        continue
    busy = (m.get("running") or 0) > 0
    stat = f"● {m.get('gen_tps') or 0:.0f} tok/s" if busy else "idle"
    host = "+".join(m.get("hosts") or [])
    ident = m.get("identity") or {}
    id_s = ""
    if ident:
        core = f"A{ident['active'].lstrip('A')}" if ident.get("active") else ident.get("params", "")
        id_s = f" · {core} {ident.get('quant', '')}".rstrip()
    line = f"{'●' if busy else '○'} {m['name']}{id_s}  {stat} · {host}"
    color = f" color={GOOD}" if busy else ""
    print(f"{line} |{color} font=Menlo size=12")

if sessions:
    print("---")
    real = [s for s in sessions if s.get("model") or (s.get("ctx_tokens") or 0) > 0]
    shown = sorted(real, key=lambda s: (not s.get("active"), -(s.get("last_ts") or 0)))[:6]
    for s in shown:
        dot = "●" if s.get("active") else "○"
        title = trim(s.get("title") or f"{s.get('model') or '?'} session", 30)
        pct = s.get("fill_pct")
        ctx = f"{fmt_n(s.get('ctx_tokens'))}/{fmt_n(s.get('ctx_ceiling'))}" + (
            f" ({pct:.0f}%)" if pct is not None else "")
        color = GOOD if s.get("active") else ""
        warn = f" color={WARN}" if (pct or 0) >= 80 else (f" color={color}" if color else "")
        print(f"{dot} {title}  {ctx} · {ago(s.get('last_ts'))} |{warn} "
              f"font=Menlo size=12 href={BASE}/#sessions")

gpu_nodes = [n for n in fleet["nodes"] if n.get("gpu") or n.get("up") is False
             or (n.get("mem_pct") or 0) > 0]
if gpu_nodes:
    print("---")
    for n in gpu_nodes:
        if n.get("up") is False:
            print(f"○ {n['label']} — DOWN | color={CRIT} font=Menlo size=12")
            continue
        g = n.get("gpu")
        if g:
            vr = f"{(g['vram_used'] or 0) / 1e9:.0f}/{(g['vram_total'] or 0) / 1e9:.0f}G"
            t = g.get("temp")
            tc = f" color={WARN}" if (t or 0) >= 75 else ""
            print(f"● {n['label']}  gpu {g['util_pct']:.0f}% · {vr} · {t:.0f}°C |{tc} "
                  f"font=Menlo size=12")
        else:
            print(f"● {n['label']}  cpu {n.get('cpu_pct') or 0:.0f}% · "
                  f"ram {n.get('mem_pct') or 0:.0f}% | font=Menlo size=12")

try:
    lib = get("/api/library")["nodes"]
except Exception:
    lib = []
if lib:
    total_g = sum(n["disk_bytes"] for n in lib) / 1e9
    total_m = sum(len(n["models"]) for n in lib)
    print("---")
    print(f"Library  {total_m} models · {total_g:.0f}G on disk | href={BASE}/#library")
    for n in lib:
        print(f"-- {n['label']}  {len(n['models'])} · {n['disk_bytes'] / 1e9:.0f}G "
              f"| font=Menlo size=12")
        for m in n["models"][:8]:
            mark = "●" if m.get("loaded") else "○"
            extra = f" color={GOOD}" if m.get("loaded") else ""
            print(f"---- {mark} {trim(m['model_id'], 40)}  "
                  f"{(m.get('size_bytes') or 0) / 1e9:.0f}G |{extra} font=Menlo size=11")

print("---")
print(f"Open dashboard | href={BASE}/")
print("Refresh | refresh=true")
_me = os.path.realpath(__file__)
_raw = ("https://raw.githubusercontent.com/NickCalabs/fleet-stat/main/"
        "clients/swiftbar/fleet-stat.30s.py")
print(f"Update plugin | bash=/bin/bash param1=-c "
      f"param2=\"curl -fsSL {_raw} -o '{_me}' && chmod +x '{_me}'\" "
      f"terminal=false refresh=true")
