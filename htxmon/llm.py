"""可选的 LLM 兜底解析：本地正则看不懂的句子，交给大模型转成规则 JSON。

默认关闭。开启方式：config.json -> llm.enabled = true 且填 api_key。
兼容任何 OpenAI 风格的 /chat/completions 接口（OpenAI、DeepSeek、通义、本地 Ollama 等）。
"""

from __future__ import annotations

import json
import re
from typing import Any

from .models import RULE_TYPES, Rule
from .netutil import http_json

SYSTEM_PROMPT = """你是交易监控规则解析器。把用户的中文/英文描述转成 JSON 数组，不要输出任何解释。
每个元素字段：
  symbol: 合约代码，形如 BTC-USDT（必须是大写币种 + "-USDT"）
  type: 取值之一 %s
  level: 价位（数字），仅 in_range/out_range 时表示区间下沿
  level2: 区间上沿（仅 in_range/out_range）
  pct: 百分比数字（near / pct_* 使用）
  window_sec: 时间窗口秒数（pct_* 使用，默认 300）
  repeat: "once" 或 "always"
规则：不要编造用户没说过的价位；如果描述里有多个条件，输出多个元素；无法解析时输出空数组 []。
""" % ",".join(sorted(RULE_TYPES))


def _extract_json(text: str) -> Any:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end > start:
        text = text[start:end + 1]
    return json.loads(text)


def parse_with_llm(text: str, cfg: dict[str, Any]) -> tuple[list[Rule], str | None]:
    """返回 (规则列表, 错误信息)。"""
    llm = cfg.get("llm", {})
    if not llm.get("enabled"):
        return [], "未启用 LLM"
    key = (llm.get("api_key") or "").strip()
    if not key:
        return [], "未配置 api_key"

    url = llm.get("base_url", "").rstrip("/") + "/chat/completions"
    payload = {
        "model": llm.get("model", "gpt-4o-mini"),
        "temperature": 0,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
    }
    try:
        resp = http_json(
            url,
            data=payload,
            headers={"Authorization": f"Bearer {key}"},
            timeout=float(llm.get("timeout_sec", 20)),
            verify=cfg.get("tls_verify", True),
        )
        content = resp["choices"][0]["message"]["content"]
        raw = _extract_json(content)
    except Exception as exc:
        return [], f"LLM 调用失败: {exc}"

    rules: list[Rule] = []
    for item in raw if isinstance(raw, list) else []:
        try:
            sym = str(item.get("symbol", "")).upper()
            if not sym.endswith("-USDT"):
                continue
            rtype = item.get("type")
            if rtype not in RULE_TYPES:
                continue
            rules.append(Rule(
                symbol=sym,
                type=rtype,
                raw=text,
                level=float(item["level"]) if item.get("level") is not None else None,
                level2=float(item["level2"]) if item.get("level2") is not None else None,
                pct=float(item["pct"]) if item.get("pct") is not None else None,
                window_sec=int(item["window_sec"]) if item.get("window_sec") else None,
                repeat=item.get("repeat") if item.get("repeat") in ("once", "always") else "once",
                note=text[:80],
            ))
        except Exception:
            continue
    if not rules:
        return [], "LLM 未能解析出有效规则"
    return rules, None
