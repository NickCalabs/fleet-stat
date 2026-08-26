#!/bin/bash
# FleetStat library collector — inventories HF-hub caches on GPU nodes over SSH
# and POSTs them to FleetStat's ingest endpoint. Runs on pve2 (cron, 6h).
# Ollama nodes (tata) are inventoried by FleetStat itself over HTTP — not here.
set -u
FS=${FLEET_STAT_URL:-http://localhost:8090}

collect() { # $1=ssh target  $2=node id  $3=hub dir
  ssh -o BatchMode=yes -o ConnectTimeout=6 "$1" \
      "du -sb --time --time-style=+%s $3/models--* 2>/dev/null" | \
  python3 -c '
import json, re, sys
node = sys.argv[1]
rows = []
for line in sys.stdin:
    parts = line.rstrip("\n").split("\t")
    if len(parts) != 3:
        continue
    size, mtime, path = parts
    name = path.rstrip("/").split("/")[-1]
    if not name.startswith("models--"):
        continue
    mid = name[len("models--"):].replace("--", "/", 1).replace("--", "/")
    q = re.search(r"(FP8|FP16|BF16|NVFP4|AWQ|GPTQ(?:-Int[48])?|Int[48]|Q\d_[A-Z_\d]+)",
                  mid, re.I)
    a = re.search(r"-A(\d+\.?\d*)B", mid)
    p = re.search(r"(\d+\.?\d*)B", mid)
    rows.append({"id": mid, "path": path, "size_bytes": int(size),
                 "mtime": float(mtime),
                 "quant": q.group(1) if q else None,
                 "params": (p.group(1) + "B" + (f" A{a.group(1)}B" if a else "")) if p else None})
print(json.dumps({"node": node, "source": "hf-cache", "models": rows}))
' "$2" | curl -s -X POST "$FS/api/library/inventory" \
             -H 'Content-Type: application/json' -d @- >/dev/null \
  && echo "ok: $2" || echo "FAIL: $2" >&2
}

# collect <ssh-target> <node-id> <hf-hub-path>, e.g.:
# collect root@10.0.0.10 gpu-box /home/me/.cache/huggingface/hub
