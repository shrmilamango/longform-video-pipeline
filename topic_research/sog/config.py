"""Loads config.yaml, falling back to config.example.yaml for any missing setting."""
import copy

import yaml

from .util import ROOT

_cache = None


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load(reload=False):
    global _cache
    if _cache is not None and not reload:
        return _cache
    with open(ROOT / "config.example.yaml", encoding="utf-8") as f:
        base = yaml.safe_load(f)
    user_path = ROOT / "config.yaml"
    if user_path.exists():
        with open(user_path, encoding="utf-8") as f:
            base = _merge(base, yaml.safe_load(f) or {})
    _cache = base
    return base


def key(name):
    return (load().get("keys", {}).get(name) or "").strip()
