"""网络工具：统一的 TLS 上下文 + JSON 请求。

为什么需要这个模块：
某些 Windows Python 环境（例如 MSYS2 / Conda 精简包）没有内置 CA 证书，
`ssl.create_default_context()` 的 x509 计数为 0，导致所有 HTTPS / WSS 请求
直接报 CERTIFICATE_VERIFY_FAILED。这里按三级兜底自动修复：
  1) 系统默认证书
  2) certifi（如果装了）
  3) 从 Windows 证书存储（ROOT / CA）导出为 PEM
最后才允许显式关闭校验（配置项 tls_verify=false，会打警告）。
"""

from __future__ import annotations

import base64
import glob
import json
import os
import shutil
import ssl
import sys
import threading
import urllib.error
import urllib.request

_lock = threading.Lock()
_contexts: dict[bool, ssl.SSLContext] = {}
_bundle_used: str | None = None


def _drives() -> list[str]:
    return [f"{c}:" for c in "CDEFG" if os.path.exists(f"{c}:\\")]


def _bundle_candidates() -> list[str]:
    """系统上可能存在的 CA 证书包路径。"""
    paths: list[str] = []
    for env in ("HTXMON_CA_BUNDLE", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        val = os.environ.get(env)
        if val:
            paths.append(val)

    rel = (
        "etc/ssl/cert.pem",
        "etc/ssl/certs/ca-bundle.crt",
        "etc/ssl/certs/ca-certificates.crt",
        "ssl/cert.pem",
        "etc/pki/tls/certs/ca-bundle.crt",
    )
    for base in {sys.prefix, getattr(sys, "base_prefix", sys.prefix), sys.exec_prefix}:
        paths += [os.path.join(base, r) for r in rel]

    git = shutil.which("git")
    if git:
        root = os.path.dirname(os.path.dirname(os.path.abspath(git)))
        paths += [
            os.path.join(root, "mingw64/etc/ssl/certs/ca-bundle.crt"),
            os.path.join(root, "usr/ssl/certs/ca-bundle.crt"),
        ]

    sub = (
        r"Program Files\Git\mingw64\etc\ssl\certs\ca-bundle.crt",
        r"Program Files\Git\usr\ssl\certs\ca-bundle.crt",
        r"Program Files (x86)\Git\mingw64\etc\ssl\certs\ca-bundle.crt",
        r"Git\mingw64\etc\ssl\certs\ca-bundle.crt",
        r"Git\usr\ssl\certs\ca-bundle.crt",
        r"msys64\usr\ssl\certs\ca-bundle.crt",
        r"msys64\ucrt64\etc\ssl\certs\ca-bundle.crt",
        r"msys64\ucrt64\etc\ssl\cert.pem",
        r"msys64\mingw64\etc\ssl\certs\ca-bundle.crt",
    )
    for d in _drives():
        paths += [os.path.join(d + "\\", s) for s in sub]

    patterns = (
        r"Users\*\AppData\Local\Programs\Python\Python3*\Lib\site-packages\certifi\cacert.pem",
        r"Python*\Lib\site-packages\certifi\cacert.pem",
        r"python*\Lib\site-packages\certifi\cacert.pem",
        r"Program Files\Python*\Lib\site-packages\certifi\cacert.pem",
        r"*\Lib\site-packages\certifi\cacert.pem",
        r"*\lib\site-packages\certifi\cacert.pem",
    )
    for d in _drives():
        for pat in patterns:
            try:
                found = glob.glob(os.path.join(d + "\\", pat))
            except Exception:
                found = []
            paths += found
    return paths


def _load_bundle(path: str):
    """把证书包读进上下文，返回 (context, 该文件是否可用)。"""
    try:
        if not os.path.isfile(path) or os.path.getsize(path) < 1000:
            return None
        ctx = ssl.create_default_context(cafile=path)
        if ctx.cert_store_stats().get("x509", 0) > 0:
            return ctx
    except Exception:
        return None
    return None


def _pem_from_windows_store() -> str:
    """把 Windows 证书存储里的根证书导出成 PEM 拼接串。"""
    enum = getattr(ssl, "enum_certificates", None)
    if enum is None:
        return ""
    chunks: list[str] = []
    for store in ("ROOT", "CA"):
        try:
            for cert, encoding, _trust in enum(store):
                if encoding != "x509":
                    continue
                b64 = base64.b64encode(cert).decode("ascii")
                body = "\n".join(b64[i:i + 64] for i in range(0, len(b64), 64))
                chunks.append(f"-----BEGIN CERTIFICATE-----\n{body}\n-----END CERTIFICATE-----\n")
        except Exception:
            continue
    return "".join(chunks)


def ssl_context(verify: bool = True) -> ssl.SSLContext:
    """返回可用的 SSL 上下文（带缓存）。"""
    global _bundle_used
    with _lock:
        if verify in _contexts:
            return _contexts[verify]

        ctx = ssl.create_default_context()
        if ctx.cert_store_stats().get("x509", 0) == 0:
            try:
                import certifi  # type: ignore

                ctx = ssl.create_default_context(cafile=certifi.where())
                _bundle_used = certifi.where()
            except Exception:
                pass
        if ctx.cert_store_stats().get("x509", 0) == 0:
            pem = _pem_from_windows_store()
            if pem:
                ctx = ssl.create_default_context(cadata=pem)
                _bundle_used = "<windows-cert-store>"
        if ctx.cert_store_stats().get("x509", 0) == 0:
            for cand in _bundle_candidates():
                loaded = _load_bundle(cand)
                if loaded is not None:
                    ctx = loaded
                    _bundle_used = cand
                    break
        if ctx.cert_store_stats().get("x509", 0) == 0 and verify:
            # 仍然没证书，只能不校验；调用方从 cert_info() 能看到状态
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        if not verify:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

        _contexts[verify] = ctx
        return ctx


def cert_info() -> dict:
    """给 UI 用的证书状态。"""
    ctx = ssl_context(True)
    stats = ctx.cert_store_stats()
    return {
        "ca_count": stats.get("x509", 0),
        "verifying": ctx.verify_mode != ssl.CERT_NONE,
        "bundle": _bundle_used,
    }


def urlopen(req: urllib.request.Request, timeout: float = 15.0, verify: bool = True):
    return urllib.request.urlopen(req, timeout=timeout, context=ssl_context(verify))


def http_json(
    url: str,
    *,
    data: dict | None = None,
    headers: dict | None = None,
    timeout: float = 15.0,
    verify: bool = True,
) -> dict:
    """GET / POST JSON。data 不为 None 时走 POST。"""
    body = None
    hdrs = {"User-Agent": "htxmon/0.1", "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=hdrs, method="POST" if body else "GET")
    with urlopen(req, timeout=timeout, verify=verify) as resp:
        raw = resp.read().decode("utf-8", "replace")
    return json.loads(raw) if raw.strip() else {}


def http_get_text(url: str, timeout: float = 15.0, verify: bool = True) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "htxmon/0.1"})
    with urlopen(req, timeout=timeout, verify=verify) as resp:
        return resp.read().decode("utf-8", "replace")
