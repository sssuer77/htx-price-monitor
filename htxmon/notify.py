"""提醒通道：界面 / 声音 / Windows 通知 / Webhook。"""

from __future__ import annotations

import html
import json
import platform
import subprocess
import threading
import time
from typing import Any, Callable

from .netutil import http_json

IS_WINDOWS = platform.system() == "Windows"

TOAST_PS = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType=WindowsRuntime] | Out-Null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml([System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($env:HTXMON_TOAST_XML)))
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('HTX 价格监控').Show($toast)
"""


class Notifier:
    """把一条告警分发到所有启用且可用的通道。"""

    def __init__(self, cfg: dict[str, Any], logger: Callable[[str, str], None] | None = None):
        self.cfg = cfg
        self.ncfg = cfg.get("notify", {})
        self.log = logger or (lambda level, msg: None)
        self._last_sound = 0.0

    # -------------------------------------------------- 对外入口
    def send(self, title: str, body: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        results: dict[str, Any] = {}
        payload = payload or {}
        for chan in ("ui", "sound", "toast", "webhook"):
            if not self.ncfg.get(chan if chan != "webhook" else "webhook"):
                results[chan] = "disabled"
                continue
            try:
                if chan == "ui":
                    results[chan] = "ok"          # 界面通道由 EventLog + SSE 完成
                elif chan == "sound":
                    results[chan] = self._sound()
                elif chan == "toast":
                    results[chan] = self._toast(title, body)
                elif chan == "webhook":
                    results[chan] = self._webhook(title, body, payload)
            except Exception as exc:
                results[chan] = f"error: {exc}"
                self.log("warn", f"{chan} 通道发送失败: {exc}")
        return results

    # -------------------------------------------------- 各通道实现
    def _sound(self) -> str:
        now = time.time()
        if now - self._last_sound < 1.0:      # 防连响
            return "throttled"
        self._last_sound = now
        if not IS_WINDOWS:
            print("\a", end="", flush=True)
            return "bell"
        try:
            import winsound

            def _beep() -> None:
                for freq, dur in ((880, 160), (1180, 160), (880, 160)):
                    winsound.Beep(freq, dur)

            threading.Thread(target=_beep, daemon=True).start()
            return "ok"
        except Exception:
            print("\a", end="", flush=True)
            return "bell"

    def _toast(self, title: str, body: str) -> str:
        if not IS_WINDOWS:
            return "unsupported"
        xml = (
            "<toast duration='short'><visual><binding template='ToastGeneric'>"
            f"<text>{html.escape(title)}</text><text>{html.escape(body)}</text>"
            "</binding></visual>"
            "<audio src='ms-winsoundevent:Notification.Default'/></toast>"
        )
        import base64
        import os

        env = dict(os.environ)
        env["HTXMON_TOAST_XML"] = base64.b64encode(xml.encode("utf-8")).decode("ascii")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", TOAST_PS],
            capture_output=True, env=env, timeout=15, creationflags=flags,
        )
        if proc.returncode != 0:
            err = (proc.stderr or b"").decode("utf-8", "replace").strip()[:200]
            raise RuntimeError(err or f"powershell 退出码 {proc.returncode}")
        return "ok"

    def _webhook(self, title: str, body: str, payload: dict[str, Any]) -> str:
        url = (self.ncfg.get("webhook") or "").strip()
        if not url.startswith("http"):
            return "no-url"
        kind = (self.ncfg.get("webhook_kind") or "generic").lower()
        text = f"{title}\n{body}"
        if kind == "wecom":
            data = {"msgtype": "text", "text": {"content": text}}
        elif kind == "dingtalk":
            data = {"msgtype": "text", "text": {"content": text}}
        elif kind == "feishu":
            data = {"msg_type": "text", "content": {"text": text}}
        elif kind == "serverchan":
            data = {"title": title, "desp": body}
        elif kind == "telegram":
            data = {"text": text}
        else:
            data = {"title": title, "text": text, "ts": int(time.time() * 1000), **payload}
        resp = http_json(url, data=data, timeout=10, verify=self.cfg.get("tls_verify", True))
        code = resp.get("errcode", resp.get("code", 0)) if isinstance(resp, dict) else 0
        if code not in (0, None):
            raise RuntimeError(f"webhook 返回 {json.dumps(resp, ensure_ascii=False)[:160]}")
        return "ok"
