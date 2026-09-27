"""极简 WebSocket 客户端（仅标准库）。

只实现监控场景够用的子集：文本帧、分片、ping/pong、close。
不实现 permessage-deflate（HTX 行情推送默认不启用）。
"""

from __future__ import annotations

import base64
import hashlib
import os
import socket
import struct
import time
from urllib.parse import urlparse

from .netutil import ssl_context

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OP_CONT, OP_TEXT, OP_BIN, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA


class WebSocketError(Exception):
    pass


class WebSocketClient:
    def __init__(
        self,
        url: str,
        timeout: float = 30.0,
        extra_headers: dict[str, str] | None = None,
        verify: bool = True,
    ):
        self.url = url
        self.timeout = timeout
        self.extra_headers = extra_headers or {}
        self.verify = verify
        self.sock: socket.socket | None = None
        self._buf = b""
        self._frag_op: int | None = None
        self._frag_data = bytearray()
        self._last_ping = 0.0

    # ------------------------------------------------------------ 连接
    def connect(self) -> None:
        u = urlparse(self.url)
        if u.scheme not in ("ws", "wss"):
            raise WebSocketError(f"不支持的协议: {u.scheme}")
        host = u.hostname or ""
        port = u.port or (443 if u.scheme == "wss" else 80)
        path = u.path or "/"
        if u.query:
            path += "?" + u.query

        raw = socket.create_connection((host, port), timeout=self.timeout)
        raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        if u.scheme == "wss":
            raw = ssl_context(verify=self.verify).wrap_socket(raw, server_hostname=host)
        self.sock = raw

        key = base64.b64encode(os.urandom(16)).decode()
        headers = [
            f"GET {path} HTTP/1.1",
            f"Host: {host}:{port}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {key}",
            "Sec-WebSocket-Version: 13",
            "User-Agent: htxmon/0.1",
        ]
        headers += [f"{k}: {v}" for k, v in self.extra_headers.items()]
        self.sock.sendall(("\r\n".join(headers) + "\r\n\r\n").encode())

        # 读取握手响应
        while b"\r\n\r\n" not in self._buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise WebSocketError("握手期间连接被关闭")
            self._buf += chunk
        head, self._buf = self._buf.split(b"\r\n\r\n", 1)
        lines = head.decode("latin-1").split("\r\n")
        if "101" not in lines[0]:
            raise WebSocketError(f"握手失败: {lines[0]}")
        resp = {}
        for line in lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                resp[k.strip().lower()] = v.strip()
        expect = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        if resp.get("sec-websocket-accept") != expect:
            raise WebSocketError("Sec-WebSocket-Accept 校验失败")

    # ------------------------------------------------------------ 收发
    def send_text(self, text: str) -> None:
        self._send_frame(OP_TEXT, text.encode("utf-8"))

    def ping(self) -> None:
        try:
            self._send_frame(OP_PING, b"hb")
            self._last_ping = time.time()
        except OSError as exc:
            raise WebSocketError(str(exc)) from exc

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        if not self.sock:
            raise WebSocketError("未连接")
        header = bytearray()
        header.append(0x80 | opcode)
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < (1 << 16):
            header.append(0x80 | 126)
            header += struct.pack("!H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack("!Q", length)
        mask = os.urandom(4)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def recv_text(self) -> str | None:
        """返回一条完整文本消息；超时返回 None。"""
        while True:
            frame = self._read_frame()
            if frame is None:
                return None
            fin, opcode, payload = frame
            if opcode == OP_CLOSE:
                raise WebSocketError("服务端关闭连接")
            if opcode == OP_PING:
                self._send_frame(OP_PONG, payload)
                continue
            if opcode == OP_PONG:
                continue
            if opcode == OP_TEXT:
                if fin:
                    return payload.decode("utf-8", "replace")
                self._frag_op, self._frag_data = OP_TEXT, bytearray(payload)
                continue
            if opcode == OP_CONT and self._frag_op == OP_TEXT:
                self._frag_data += payload
                if fin:
                    data = bytes(self._frag_data)
                    self._frag_op, self._frag_data = None, bytearray()
                    return data.decode("utf-8", "replace")
                continue
            # 其它帧（二进制等）忽略

    def _read_exact(self, n: int) -> bytes | None:
        while len(self._buf) < n:
            try:
                chunk = self.sock.recv(65536)  # type: ignore[union-attr]
            except socket.timeout:
                return None
            except OSError as exc:
                raise WebSocketError(str(exc)) from exc
            if not chunk:
                raise WebSocketError("连接已断开")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _read_frame(self) -> tuple[bool, int, bytes] | None:
        head = self._read_exact(2)
        if head is None:
            return None
        b1, b2 = head[0], head[1]
        fin = bool(b1 & 0x80)
        opcode = b1 & 0x0F
        masked = bool(b2 & 0x80)
        length = b2 & 0x7F
        if length == 126:
            ext = self._read_exact(2)
            if ext is None:
                return None
            length = struct.unpack("!H", ext)[0]
        elif length == 127:
            ext = self._read_exact(8)
            if ext is None:
                return None
            length = struct.unpack("!Q", ext)[0]
        mask = b""
        if masked:
            mask = self._read_exact(4) or b""
        payload = b""
        if length:
            payload = self._read_exact(length) or b""
        if masked and mask:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return fin, opcode, payload

    def close(self) -> None:
        try:
            if self.sock:
                self._send_frame(OP_CLOSE, b"")
        except Exception:
            pass
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass
        self.sock = None
        self._buf = b""

    def __enter__(self) -> "WebSocketClient":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()
