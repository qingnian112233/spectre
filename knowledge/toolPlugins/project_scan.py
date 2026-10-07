#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""项目文件树感知: 自动识别项目根/技术栈/入口/清单文件, 输出可注入上下文的结构摘要。
用法: python3 project_scan.py '{"op":"context","path":"/opt/deepseek-bot"}'
op: context(默认,紧凑上下文摘要) / scan(全量报告) / tree(只出目录树) / manifests(只看清单文件)
"""
import sys, os, json, subprocess

IGNORE = {".git", "node_modules", "__pycache__", ".venv", "venv", "env", "dist", "build",
          "target", ".idea", ".vscode", "vendor", ".next", ".nuxt", "coverage", ".mypy_cache",
          ".pytest_cache", ".cache", "logs", "log", "tmp", ".tox", "site-packages", ".egg-info",
          "bower_components", ".sass-cache", "out", ".gradle", ".terraform", "snapshots"}
CODE_EXT = {
    ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript",
    ".jsx": "JavaScript", ".php": "PHP", ".go": "Go", ".rs": "Rust", ".java": "Java",
    ".rb": "Ruby", ".c": "C", ".h": "C/C++", ".cpp": "C++", ".cs": "C#", ".swift": "Swift",
    ".kt": "Kotlin", ".sh": "Shell", ".vue": "Vue", ".html": "HTML", ".css": "CSS",
    ".scss": "SCSS", ".sql": "SQL", ".lua": "Lua", ".dart": "Dart", ".r": "R",
}
MANIFESTS = ["package.json", "requirements.txt", "pyproject.toml", "setup.py", "go.mod",
             "Cargo.toml", "composer.json", "pom.xml", "build.gradle", "Gemfile",
             "Dockerfile", "docker-compose.yml", "docker-compose.yaml", ".env.example",
             "Makefile", "tsconfig.json", "vite.config.js", "vite.config.ts", "next.config.js"]
ENTRY_NAMES = ["main.py", "app.py", "bot.py", "index.js", "index.ts", "server.js", "server.py",
               "manage.py", "main.go", "main.rs", "index.php", "run.py", "start.py", "cli.py"]
MAX_FILES = 40000


def sh_out(args, cwd=None):
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=True, timeout=20)
        return p.stdout.decode("utf-8", "replace").strip() if p.returncode == 0 else ""
    except Exception:
        return ""


def find_root(path):
    path = os.path.abspath(path or ".")
    if not os.path.isdir(path):
        path = os.path.dirname(path)
    r = sh_out(["git", "-C", path, "rev-parse", "--show-toplevel"])
    if r and os.path.isdir(r):
        return r, "git"
    cur = path
    for _ in range(30):
        if os.path.isdir(os.path.join(cur, ".git")):
            return cur, "git(dir)"
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    # 无 VCS: 以含 manifest 的最近目录为准, 否则用传入目录
    cur = path
    for _ in range(30):
        for m in MANIFESTS:
            if os.path.exists(os.path.join(cur, m)):
                return cur, "manifest"
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return path, "dir"


def scan_tree(root, max_depth=3):
    tree_lines = []
    counts = {}
    size_total = 0
    nfiles = 0
    n = 0

    def walk(d, depth, prefix=""):
        nonlocal size_total, nfiles, n
        if depth > max_depth or n > MAX_FILES:
            return
        try:
            items = sorted(os.listdir(d))
        except Exception:
            return
        dirs = [i for i in items if os.path.isdir(os.path.join(d, i)) and i not in IGNORE]
        files = [i for i in items if os.path.isfile(os.path.join(d, i))]
        for f in files:
            n += 1; nfiles += 1
            ext = os.path.splitext(f)[1].lower()
            try:
                size_total += os.path.getsize(os.path.join(d, f))
            except Exception:
                pass
            if ext in CODE_EXT:
                counts[CODE_EXT[ext]] = counts.get(CODE_EXT[ext], 0) + 1
        if depth <= max_depth:
            for i in dirs:
                tree_lines.append("%s%s/" % ("  " * depth, i))
                walk(os.path.join(d, i), depth + 1)
        # 当前层文件只列代表
        if depth <= max_depth and files:
            show = files[:8]
            tree_lines.append("%s%s" % ("  " * depth, ", ".join(show) + (" ...(%d)" % len(files) if len(files) > 8 else "")))
        for i in dirs:
            pass

    walk(root, 0)
    return tree_lines, counts, size_total, nfiles


def read_manifests(root, max_depth=2):
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        depth = dirpath[len(root):].count(os.sep)
        dirnames[:] = [d for d in dirnames if d not in IGNORE]
        if depth >= max_depth:
            dirnames[:] = []
        for m in MANIFESTS:
            if m in filenames and len(found) < 12:
                p = os.path.join(dirpath, m)
                info = {"file": os.path.relpath(p, root), "size": os.path.getsize(p)}
                try:
                    if m == "package.json":
                        d = json.load(open(p, encoding="utf-8", errors="replace"))
                        info["name"] = d.get("name")
                        info["scripts"] = list((d.get("scripts") or {}).keys())[:12]
                        deps = list(d.get("dependencies") or {})
                        dev = list(d.get("devDependencies") or {})
                        info["deps"] = deps[:20]
                        info["dep_count"] = len(deps) + len(dev)
                        fw = [x for x in deps if x in ("express", "koa", "fastify", "next", "react",
                              "vue", "nuxt", "nest", "@nestjs/core", "electron", "vite", "svelte")]
                        if fw:
                            info["frameworks"] = fw
                    elif m == "requirements.txt":
                        lines = [l.strip() for l in open(p, encoding="utf-8", errors="replace") if l.strip() and not l.startswith("#")]
                        info["pkgs"] = [l.split("==")[0].split(">=")[0].strip() for l in lines[:25]]
                        info["pkg_count"] = len(lines)
                    elif m == "composer.json":
                        d = json.load(open(p, encoding="utf-8", errors="replace"))
                        info["name"] = d.get("name")
                        info["require"] = list((d.get("require") or {}).keys())[:20]
                    elif m in ("go.mod", "Cargo.toml", "pyproject.toml"):
                        txt = open(p, encoding="utf-8", errors="replace").read()
                        info["head"] = txt[:400]
                    elif m in ("Dockerfile", "docker-compose.yml", "Makefile"):
                        txt = open(p, encoding="utf-8", errors="replace").read()
                        info["head"] = txt[:300]
                except Exception as e:
                    info["err"] = str(e)
                found.append(info)
    return found


def find_entries(root, max_depth=3):
    hits = []
    for dirpath, dirnames, filenames in os.walk(root):
        depth = dirpath[len(root):].count(os.sep)
        dirnames[:] = [d for d in dirnames if d not in IGNORE]
        if depth >= max_depth:
            dirnames[:] = []
        for f in filenames:
            if f in ENTRY_NAMES:
                hits.append(os.path.relpath(os.path.join(dirpath, f), root))
        if len(hits) > 20:
            return hits[:20]
    return hits


def read_docs(root):
    docs = {}
    for f in ("README.md", "README.txt", "README", "CLAUDE.md", "AGENTS.md", "AGENT.md"):
        p = os.path.join(root, f)
        if os.path.exists(p):
            try:
                docs[f] = open(p, encoding="utf-8", errors="replace").read()[:1500]
            except Exception:
                pass
    return docs


def vcs_info(root):
    info = {}
    if os.path.isdir(os.path.join(root, ".git")):
        br = sh_out(["git", "-C", root, "rev-parse", "--abbrev-ref", "HEAD"])
        if br:
            info["branch"] = br
        last = sh_out(["git", "-C", root, "log", "-1", "--pretty=%h %ad %an %s", "--date=short"])
        if last:
            info["last_commit"] = last
        st = sh_out(["git", "-C", root, "status", "--porcelain"])
        info["dirty_files"] = len([l for l in st.split("\n") if l.strip()])
    return info


def main():
    a = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    op = (a.get("op") or "context").lower()
    root, how = find_root(a.get("path") or ".")
    depth = int(a.get("depth") or 3)
    if op == "manifests":
        print(json.dumps(read_manifests(root), ensure_ascii=False, indent=1)[:8000]); return

    tree_lines, counts, size_total, nfiles = scan_tree(root, depth)
    if op == "tree":
        print("ROOT: %s\n" % root + "\n".join(tree_lines[:200])); return

    manifests = read_manifests(root)
    entries = find_entries(root)
    docs = read_docs(root)
    vcs = vcs_info(root)
    langs = sorted(counts.items(), key=lambda x: -x[1])[:8]

    if op == "scan":
        print(json.dumps({"root": root, "detected_by": how, "files": nfiles,
                          "size_mb": round(size_total / 1048576, 2), "langs": langs,
                          "vcs": vcs, "manifests": manifests, "entries": entries,
                          "tree": tree_lines[:150], "docs": {k: v[:400] for k, v in docs.items()}},
                         ensure_ascii=False, indent=1)[:12000]); return

    # context: 紧凑摘要, 直接可注入
    out = []
    out.append("### 项目: %s  (根=%s, 探测=%s)" % (os.path.basename(root) or root, root, how))
    out.append("规模: %d 文件 / %.2f MB" % (nfiles, size_total / 1048576))
    if langs:
        out.append("语言TOP: " + ", ".join("%s(%d)" % (k, v) for k, v in langs))
    if vcs:
        out.append("VCS: " + json.dumps(vcs, ensure_ascii=False))
    if entries:
        out.append("入口候选: " + ", ".join(entries[:12]))
    for m in manifests[:6]:
        line = "- " + m["file"]
        if m.get("name"):
            line += " name=%s" % m["name"]
        if m.get("frameworks"):
            line += " fw=%s" % ",".join(m["frameworks"])
        if m.get("dep_count"):
            line += " deps=%d" % m["dep_count"]
        if m.get("pkg_count"):
            line += " pkgs=%d" % m["pkg_count"]
        if m.get("scripts"):
            line += " scripts=%s" % ",".join(m["scripts"][:8])
        if m.get("pkgs"):
            line += " pkgs=%s" % ",".join(m["pkgs"][:10])
        out.append(line)
    out.append("目录树(≤%d层):" % depth)
    out += ["  " + l for l in tree_lines[:60]]
    for k, v in docs.items():
        out.append("--- %s (前400字) ---" % k)
        out.append(v[:400].replace("\r", ""))
    print("\n".join(out)[:9000])


if __name__ == "__main__":
    main()
