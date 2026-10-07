"""file_to_text - 把任意文件转成文本，喂给模型读。
支持: 文本/PDF/Office(WPS)/图片/音频/视频/压缩包。
依赖已装: ffmpeg soffice pandoc pdftotext tesseract 7z faster-whisper
"""
import os, re, subprocess, shutil, tempfile

_MAX_CHARS = 6000      # 文本超长截断
_MAX_AUDIO_SEC = 300   # 音频超过5分钟不转(CPU慢)
_WHISPER_READY = None


def _run(cmd, timeout=60):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout, r.stderr, r.returncode
    except Exception as e:
        return "", str(e), -1


def _ext_by_mime(fp):
    """先用 file 探真实类型"""
    out, _, _ = _run(["file", "-b", "--mime-type", fp], 10)
    return out.strip().lower()


def _probe_duration(fp):
    out, _, rc = _run(["ffprobe", "-v", "error", "-show_entries",
                       "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", fp], 20)
    try:
        return float(out.strip())
    except Exception:
        return 0.0


def _audio_to_text(fp):
    """音频/视频 → 16k单声道wav → faster-whisper"""
    global _WHISPER_READY
    dur = _probe_duration(fp)
    if dur <= 0:
        return None
    if dur > _MAX_AUDIO_SEC:
        return f"[音频时长 {dur:.0f}s 超过上限 {_MAX_AUDIO_SEC}s，跳过转写，请截取关键片段]"
    wav = tempfile.mktemp(suffix=".wav")
    _, err, rc = _run(["ffmpeg", "-y", "-i", fp, "-ar", "16000", "-ac", "1",
                       "-c:a", "pcm_s16le", wav], 120)
    if rc != 0 or not os.path.exists(wav):
        return None
    try:
        if _WHISPER_READY is None:
            from faster_whisper import WhisperModel
            model = WhisperModel("large-v3", device="cpu", compute_type="int8")
            _WHISPER_READY = model
        segments, info = _WHISPER_READY.transcribe(
            wav, vad_filter=True, beam_size=5,
            condition_on_previous_text=False)
        lang = f"({info.language})" if info and info.language else ""
        lines = [s.text.strip() for s in segments if s.text.strip()]
        if not lines:
            return None
        body = "\n".join(lines)
        return f"[音频转写{lang} 时长{dur:.0f}s]\n{body}"
    except Exception as e:
        return f"[音频转写失败: {e}]"
    finally:
        try:
            os.remove(wav)
        except Exception:
            pass


def _pdf_to_text(fp):
    out, _, rc = _run(["pdftotext", "-layout", fp, "-"], 60)
    if rc == 0 and out.strip():
        return out.strip()
    return None


def _office_to_text(fp, ext):
    """Office/WPS → txt (soffice) 或 pandoc"""
    # docx 用 pandoc 效果好
    if ext in (".docx", ".md", ".html", ".htm", ".epub", ".odt"):
        out, _, rc = _run(["pandoc", fp, "-t", "plain"], 60)
        if rc == 0 and out.strip():
            return out.strip()
    # 通用兜底: soffice 转 txt
    tmpdir = tempfile.mkdtemp()
    _, _, rc = _run(["soffice", "--headless", "--convert-to", "txt:Text",
                     "--outdir", tmpdir, fp], 120)
    if rc == 0:
        for f in os.listdir(tmpdir):
            if f.endswith(".txt"):
                p = os.path.join(tmpdir, f)
                txt = open(p, "r", errors="replace").read()
                shutil.rmtree(tmpdir, ignore_errors=True)
                return txt.strip()
    shutil.rmtree(tmpdir, ignore_errors=True)
    return None


def _archive_to_text(fp):
    """压缩包: 列目录 + 抽取文本文件"""
    out, _, rc = _run(["7z", "l", "-ba", fp], 60)
    if rc != 0:
        # 试试 unzip
        out, _, rc = _run(["unzip", "-l", fp], 30)
        if rc != 0:
            return None
        lines = out.splitlines()
        names = []
        for ln in lines[3:-2]:
            parts = ln.split()
            if parts:
                names.append(parts[-1])
        entries = names
        _extract = ["unzip", "-o", fp]
    else:
        entries = []
        for ln in out.splitlines():
            m = re.search(r"\s(\S+)$", ln)
            if m:
                entries.append(m.group(1))
        _extract = ["7z", "x", "-y", fp]

    tmpdir = tempfile.mkdtemp()
    try:
        _, _, _ = _run(_extract + ["-o" + tmpdir] if _extract[0] == "7z" else _extract + ["-d", tmpdir], 120)
        texts = []
        for root, _, files in os.walk(tmpdir):
            for f in files:
                p = os.path.join(root, f)
                rel = os.path.relpath(p, tmpdir)
                ext = os.path.splitext(f)[1].lower()
                if ext in (".txt", ".md", ".csv", ".json", ".log", ".xml", ".html", ".py", ".js", ".yaml", ".yml", ".ini", ".conf"):
                    try:
                        txt = open(p, "r", errors="replace").read()[:2000]
                        texts.append(f"--- {rel} ---\n{txt}")
                    except Exception:
                        pass
        listing = "\n".join(entries[:200])
        head = f"[压缩包 {len(entries)} 个条目]\n{listing}"
        if texts:
            head += "\n\n[文本文件内容]\n" + "\n".join(texts)
        return head
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def file_to_text(fp, fname=""):
    """主入口: 返回文本描述，失败返回 None"""
    ext = os.path.splitext(fname)[1].lower()
    mime = _ext_by_mime(fp)

    # 1. Office/WPS 优先(其 mime 含 "xml" 子串，必须在文本判断之前)
    OFFICE_EXTS = {".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt",
                   ".ods", ".odp", ".wps", ".et", ".dps", ".epub", ".mobi"}
    if ext in OFFICE_EXTS:
        t = _office_to_text(fp, ext)
        return t[:_MAX_CHARS] if t else None

    # 2. 纯文本
    TEXT_EXTS = {".txt", ".md", ".csv", ".json", ".log", ".xml", ".html", ".htm",
                 ".py", ".js", ".ts", ".yaml", ".yml", ".ini", ".conf", ".cfg",
                 ".sh", ".c", ".cpp", ".h", ".java", ".go", ".rs", ".sql"}
    if ext in TEXT_EXTS or mime.startswith("text/") or "json" in mime or mime.endswith("+xml"):
        try:
            txt = open(fp, "r", encoding="utf-8", errors="replace").read()
            return txt[:_MAX_CHARS]
        except Exception:
            pass

    # 3. 图片
    if mime.startswith("image/"):
        return "[图片文件，已走 OCR/视觉链路]"

    # 4. PDF
    if "pdf" in mime or ext == ".pdf":
        t = _pdf_to_text(fp)
        return t[:_MAX_CHARS] if t else "[PDF 无文本层(扫描版)，建议转图片走 OCR]"

    # 5. 音频/视频
    if mime.startswith("audio/") or mime.startswith("video/") or ext in (".mp3", ".flac", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".mp4", ".mkv", ".avi", ".mov", ".webm", ".mpeg", ".wma"):
        return _audio_to_text(fp)

    # 6. 压缩包
    if ext in (".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".tgz", ".zst"):
        t = _archive_to_text(fp)
        return t if t else None

    # 7. 兜底: 试试直接 utf-8 读
    try:
        return open(fp, "r", encoding="utf-8", errors="replace").read()[:_MAX_CHARS]
    except Exception:
        pass

    return None
