#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""持久交互终端插件 (tmux 后端)
用法: python3 shell_session.py '{"op":"send","session":"ai","cmd":"ls -la","wait":1.5}'
op: new / send / capture / kill / list / sendline
"""
import sys, json, subprocess, time, shlex

DEFAULT_SESS = "ai"


def run(args, timeout=20):
    try:
        p = subprocess.run(args, capture_output=True, timeout=timeout)
        return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except Exception as e:
        return -2, "", str(e)


def sess_args(name):
    return ["tmux", "-t", name]


def ensure(name):
    rc, out, err = run(["tmux", "has-session", "-t", name])
    return rc == 0


def capture(name, lines=300):
    rc, out, err = run(["tmux", "capture-pane", "-pt", name, "-S", "-%d" % int(lines)])
    if rc != 0:
        return err
    # 去掉尾部空洞行(空 pane 会返回成片空行)
    out = out.rstrip("\n")
    while out.endswith("\n\n"):
        out = out[:-1]
    out = "\n".join(l for l in out.split("\n") if l.strip())
    return out


def main():
    try:
        a = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    except Exception as e:
        print("参数JSON解析失败: %s" % e); return
    op = (a.get("op") or "capture").lower()
    name = a.get("session") or DEFAULT_SESS

    if op == "list":
        rc, out, err = run(["tmux", "ls"])
        print(out or err); return

    if op == "new":
        if ensure(name):
            print("[session %s 已存在]\n%s" % (name, capture(name)))
            return
        w = int(a.get("width") or 220); h = int(a.get("height") or 50)
        rc, out, err = run(["tmux", "new-session", "-d", "-s", name, "-x", str(w), "-y", str(h)])
        if rc != 0:
            print("创建失败: %s" % err); return
        run(["tmux", "set-option", "-t", name, "remain-on-exit", "on"])
        cwd = a.get("cwd")
        if cwd:
            run(["tmux", "send-keys", "-t", name, "-l", "cd %s" % cwd])
            run(["tmux", "send-keys", "-t", name, "Enter"])
            time.sleep(float(a.get("wait") or 0.5))
        print("[session %s 已就绪]\n%s" % (name, capture(name)))
        return

    if op == "kill":
        rc, out, err = run(["tmux", "kill-session", "-t", name])
        print("已结束 session %s" % name if rc == 0 else "kill失败: %s" % err); return

    # send / sendline / capture 需要会话存在
    if not ensure(name):
        print("session %s 不存在，先 op=new 创建" % name); return

    if op == "capture":
        print(capture(name, a.get("lines") or 300)); return

    if op in ("send", "sendline"):
        cmd = a.get("cmd") or ""
        if cmd:
            # -l 原样发送，避免 tmux 解释特殊键
            run(["tmux", "send-keys", "-t", name, "-l", cmd])
            run(["tmux", "send-keys", "-t", name, "Enter"])
        wait = a.get("wait")
        try:
            wait = float(wait) if wait is not None else 1.2
        except Exception:
            wait = 1.2
        time.sleep(min(max(wait, 0.1), 60))
        # 可选：发送后继续追加多行命令
        extra = a.get("then")
        if extra:
            for line in str(extra).split("\n"):
                if not line.strip():
                    continue
                run(["tmux", "send-keys", "-t", name, "-l", line])
                run(["tmux", "send-keys", "-t", name, "Enter"])
                time.sleep(0.4)
        print(capture(name, a.get("lines") or 300)); return

    print("未知 op: %s (支持 new/send/capture/kill/list)" % op)


if __name__ == "__main__":
    main()
