from __future__ import annotations
from pathlib import Path
import tomllib

DEFAULT_RULES = Path(__file__).resolve().parents[2] / "config" / "rules.toml"

def load_rules(path: str | Path | None = None) -> dict:
    p = Path(path) if path else DEFAULT_RULES
    with p.open("rb") as f:
        return tomllib.load(f)

def get_rule(rules: dict, dotted: str):
    value = rules
    for key in dotted.split("."):
        value = value[key]
    return value
