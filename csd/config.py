from __future__ import annotations

import copy
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT = ROOT / "configs" / "default.yaml"


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load(path: str | Path | None = None) -> dict:
    cfg = yaml.safe_load(DEFAULT.read_text(encoding="utf-8"))
    if path:
        cfg = _merge(cfg, yaml.safe_load(Path(path).read_text(encoding="utf-8")))
    return cfg


def resolve(p: str | Path) -> Path:
    """Paths in the config are relative to the project root."""
    p = Path(p)
    return p if p.is_absolute() else ROOT / p
