#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MCP 常驻守护进程: 拉起一个 MCP server(stdio), 代理其 JSON-RPC, 对外暴露 unix socket。
用法: python3 mcp_daemon.py <config.json>
config: {"name":"filesystem","command":"npx","args":["-y","@modelcontextprotocol/server-filesystem","/tmp"],"env":{}}
socket: /tmp/mcp_<name>.sock   每次一行 JSON 请求 -> 一行 JSON 响应
"""
import sys, os, json, socket, subprocess, threading, time, signal

def log(*a):
    pass

class MDaemon:
    def __init__(self, cfg):
        self.cfg = cfg
        self.name = cfg["name"]
        self.sock_path = "/tmp/mcp_%s.sock" % self.name
        self.pid_path = "/tmp/mcp_%s.pid" % self.name
        self.proc = None
        self.next_id = 1
        self.lock = threading.RLock()  # 可重入: rpc->spawn->rpc 会重入
        self.buf = b""

    # ---------- 与 server 通信 ----------
    def _read_msg(self, timeout=60):
        """按行读 JSON-RPC 响应, 跳过非 JSON 噪声行"""
        end = time.time() + timeout
        while time.time() < end:
            nl = self.buf.find(b"\n")
            if nl >= 0:
                line = self.buf[:nl]
                self.buf = self.buf[nl + 1:]
                line = line.strip()
                if not line:
                    continue
                try:
                    return json.loads(line.decode("utf-8", "replace"))
                except Exception:
                    continue
            chunk = self.proc.stdout.read1(65536) if hasattr(self.proc.stdout, "read1") else None
            if chunk is None:
                chunk = os.read(self.proc.stdout.fileno(), 65536)
            if not chunk:
                return {"__eof__": True}
            self.buf += chunk
        return {"__timeout__": True}

    def _write(self, obj):
        data = json.dumps(obj, ensure_ascii=False) + "\n"
        self.proc.stdin.write(data.encode("utf-8"))
        self.proc.stdin.flush()

    def rpc(self, method, params=None, notify=False, timeout=60):
        with self.lock:
            if self.proc is None or self.proc.poll() is not None:
                self.spawn()
            msg = {"jsonrpc": "2.0", "method": method}
            if params is not None:
                msg["params"] = params
            if notify:
                self._write(msg)
                return {"ok": True, "notified": method}
            mid = self.next_id
            self.next_id += 1
            msg["id"] = mid
            self._write(msg)
            end = time.time() + timeout
            while time.time() < end:
                resp = self._read_msg(timeout=max(1, int(end - time.time())))
                if resp.get("__eof__"):
                    return {"error": {"message": "server 已退出(EOF)"}}
                if resp.get("__timeout__"):
                    return {"error": {"message": "等待 server 响应超时"}}
                if resp.get("id") == mid:
                    return resp
                # 不是本次的响应(maybe 通知), 继续读
            return {"error": {"message": "超时"}}

    def spawn(self):
        env = dict(os.environ)
        env.update(self.cfg.get("env") or {})
        self.proc = subprocess.Popen(
            [self.cfg["command"]] + (self.cfg.get("args") or []),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=env, bufsize=0,
        )
        self.buf = b""
        # 握手
        self.rpc("initialize", {
            "protocolVersion": self.cfg.get("protocolVersion", "2024-11-05"),
            "capabilities": {},
            "clientInfo": {"name": "mcp_bridge", "version": "1.0"},
        })
        self.rpc("notifications/initialized", None, notify=True)
        return True

    # ---------- socket 服务 ----------
    def serve(self):
        try:
            os.unlink(self.sock_path)
        except OSError:
            pass
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(self.sock_path)
        srv.listen(16)
        with open(self.pid_path, "w") as f:
            f.write(str(os.getpid()))
        try:
            while True:
                conn, _ = srv.accept()
                threading.Thread(target=self.handle, args=(conn,), daemon=True).start()
        finally:
            try:
                os.unlink(self.sock_path)
            except OSError:
                pass

    def handle(self, conn):
        try:
            f = conn.makefile("rwb")
            line = f.readline()
            if not line:
                return
            req = json.loads(line.decode("utf-8", "replace"))
            op = req.get("op")
            if op == "ping":
                resp = {"ok": True, "name": self.name, "alive": self.proc is not None and self.proc.poll() is None}
            elif op == "stop":
                resp = {"ok": True}
                f.write((json.dumps(resp) + "\n").encode()); f.flush(); conn.close()
                os._exit(0)
            else:
                r = self.rpc(req.get("method"), req.get("params"), notify=bool(req.get("notify")),
                             timeout=int(req.get("timeout") or 60))
                resp = {"ok": True, "resp": r}
            f.write((json.dumps(resp, ensure_ascii=False) + "\n").encode("utf-8"))
            f.flush()
        except Exception as e:
            try:
                conn.sendall((json.dumps({"ok": False, "error": str(e)}) + "\n").encode())
            except Exception:
                pass
        finally:
            try:
                conn.close()
            except Exception:
                pass


def main():
    cfg = json.load(open(sys.argv[1]))
    d = MDaemon(cfg)
    try:
        d.spawn()
    except Exception as e:
        # 启动失败也起 socket, 让调用方能拿到错误
        d.proc = None
    signal.signal(signal.SIGTERM, lambda *a: os._exit(0))
    d.serve()


if __name__ == "__main__":
    main()
