import os
import yaml

_DEFAULT_PATH = os.path.join(os.path.dirname(__file__), "..", "config.yaml")


def load_config():
    path = os.environ.get("FC_CONFIG", _DEFAULT_PATH)
    with open(path) as f:
        cfg = yaml.safe_load(f)

    models_by_name = {}
    for m in cfg.get("models", []):
        models_by_name[m["name"]] = m
        for a in m.get("aliases") or []:
            models_by_name.setdefault(a, m)
    cfg["_models_by_name"] = models_by_name

    ip_to_harness = {}
    alias_to_harness = {}
    for h in cfg.get("harnesses", []):
        for ip in h.get("ips") or []:
            ip_to_harness[ip] = h["id"]
        if h.get("key_alias"):
            alias_to_harness[h["key_alias"]] = h["id"]
    cfg["_ip_to_harness"] = ip_to_harness
    cfg["_alias_to_harness"] = alias_to_harness
    cfg["_master_harness"] = next(
        (h["id"] for h in cfg.get("harnesses", []) if h.get("master_key")), "admin"
    )
    return cfg


def resolve_harness(cfg, key_alias, requester_ip, api_key):
    if key_alias and key_alias in cfg["_alias_to_harness"]:
        return cfg["_alias_to_harness"][key_alias]
    if requester_ip and requester_ip in cfg["_ip_to_harness"]:
        return cfg["_ip_to_harness"][requester_ip]
    if api_key == "litellm_proxy_master_key":
        return cfg["_master_harness"]
    return "other"


def model_ctx(cfg, model_name):
    m = cfg["_models_by_name"].get(model_name)
    return m["ctx"] if m else None
