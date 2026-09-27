"""配置加载与保存。"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
EXAMPLE_PATH = ROOT / "config.example.json"
DATA_DIR = ROOT / "data"

DEFAULTS: dict[str, Any] = {
    "host": "127.0.0.1",
    "port": 8971,
    "open_browser": True,
    "symbols": ["BTC-USDT", "ETH-USDT", "SOL-USDT"],
    "rest_base": "https://api.hbdm.com",
    "ws_url": "wss://api.hbdm.com/linear-swap-ws",
    "enable_ws": True,
    "poll_interval_sec": 3.0,
    "history_bootstrap_min": 120,
    "tls_verify": True,
    "notify": {
        "ui": True,
        "sound": True,
        "toast": True,
        "webhook": "",
        "webhook_kind": "generic",
        "log_file": "data/events.jsonl",
    },
    "llm": {
        "enabled": False,
        "base_url": "https://api.openai.com/v1",
        "api_key": "",
        "model": "gpt-4o-mini",
        "timeout_sec": 20,
    },
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config() -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cfg = copy.deepcopy(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            cfg = _merge(cfg, json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception as exc:  # 配置坏了也要能启动
            print(f"[config] 读取 config.json 失败，使用默认配置: {exc}")
    return cfg


def save_config(cfg: dict[str, Any]) -> None:
    CONFIG_PATH.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def ensure_example() -> None:
    if not EXAMPLE_PATH.exists():
        EXAMPLE_PATH.write_text(
            json.dumps(DEFAULTS, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def data_path(rel: str) -> Path:
    p = Path(rel)
    if not p.is_absolute():
        p = ROOT / p
    p.parent.mkdir(parents=True, exist_ok=True)
    return p
