"""规则与事件日志的本地持久化（JSON / JSONL，原子写入）。"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from .config import DATA_DIR, data_path
from .models import Rule

RULES_PATH = DATA_DIR / "rules.json"
MAX_EVENTS = 500


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


class RuleStore:
    """规则仓库：内存 + rules.json 双写。"""

    def __init__(self, path: Path | None = None):
        self.path = path or RULES_PATH
        self._lock = threading.RLock()
        self._rules: list[Rule] = []
        self.load()

    def load(self) -> None:
        with self._lock:
            self._rules = []
            if not self.path.exists():
                return
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                for item in raw.get("rules", []):
                    try:
                        self._rules.append(Rule.from_dict(item))
                    except Exception:
                        continue
            except Exception as exc:
                print(f"[store] rules.json 读取失败: {exc}")

    def save(self) -> None:
        with self._lock:
            payload = {"version": 1, "updated_at": time.time(),
                       "rules": [r.to_dict() for r in self._rules]}
            _atomic_write(self.path, json.dumps(payload, ensure_ascii=False, indent=2))

    # ---- CRUD ----
    def all(self) -> list[Rule]:
        with self._lock:
            return list(self._rules)

    def active(self) -> list[Rule]:
        with self._lock:
            return [r for r in self._rules if r.status == "active"]

    def add(self, rule: Rule) -> Rule:
        with self._lock:
            self._rules.append(rule)
            self.save()
            return rule

    def add_many(self, rules: list[Rule]) -> list[Rule]:
        with self._lock:
            self._rules.extend(rules)
            self.save()
            return rules

    def get(self, rule_id: str) -> Rule | None:
        with self._lock:
            for r in self._rules:
                if r.id == rule_id:
                    return r
        return None

    def remove(self, rule_id: str) -> bool:
        with self._lock:
            before = len(self._rules)
            self._rules = [r for r in self._rules if r.id != rule_id]
            changed = len(self._rules) != before
            if changed:
                self.save()
            return changed

    def clear(self, status: str | None = None) -> int:
        with self._lock:
            before = len(self._rules)
            if status:
                self._rules = [r for r in self._rules if r.status != status]
            else:
                self._rules = []
            self.save()
            return before - len(self._rules)

    def update(self, rule: Rule) -> None:
        with self._lock:
            for i, r in enumerate(self._rules):
                if r.id == rule.id:
                    self._rules[i] = rule
                    self.save()
                    return


class EventLog:
    """事件日志：内存环形缓冲 + JSONL 落盘。"""

    def __init__(self, log_file: str | None = None, max_events: int = MAX_EVENTS):
        self.events: deque[dict[str, Any]] = deque(maxlen=max_events)
        self._lock = threading.Lock()
        self.path = data_path(log_file) if log_file else None
        self._seq = 0

    def add(self, kind: str, message: str, **extra: Any) -> dict[str, Any]:
        with self._lock:
            self._seq += 1
            ev = {"seq": self._seq, "ts": time.time(), "kind": kind, "message": message}
            ev.update(extra)
            self.events.append(ev)
            if self.path:
                try:
                    with self.path.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
                except Exception:
                    pass
            return ev

    def since(self, seq: int) -> list[dict[str, Any]]:
        with self._lock:
            return [e for e in self.events if e["seq"] > seq]

    def tail(self, n: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.events)[-n:]

    @property
    def last_seq(self) -> int:
        return self._seq
