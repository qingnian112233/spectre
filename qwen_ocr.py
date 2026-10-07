"""Qwen VL OCR - 阿里云百炼视觉模型识别文字
优先级: Qwen API → 失败回退 Tesseract
"""
import os, base64, hashlib, io, json, time
import httpx
from PIL import Image

# 从 .env 读取，兼容两种key名
QWEN_API_KEY = os.getenv("QWEN_API_KEY") or os.getenv("DASHSCOPE_API_KEY") or ""
QWEN_MODEL = os.getenv("QWEN_MODEL", "qwen3-vl-flash")
QWEN_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"

_cache = {}
_cache_hits = 0

def _img_hash(img: Image.Image) -> str:
    """图片内容哈希，用于去重缓存"""
    small = img.resize((32, 32))
    return hashlib.md5(small.tobytes()).hexdigest()

def _b64(img: Image.Image) -> str:
    buf = io.BytesIO()
    # 压缩: 最长边800px + JPEG质量70 → 更快更省token(v7提速)
    img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > 800:
        ratio = 800 / max(w, h)
        img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
    img.save(buf, format="JPEG", quality=70)
    return base64.b64encode(buf.getvalue()).decode()

def ocr_qwen(img: Image.Image, timeout=30) -> str:
    """调用Qwen VL识别图片文字"""
    global _cache_hits
    if not QWEN_API_KEY:
        return None
    h = _img_hash(img)
    if h in _cache:
        _cache_hits += 1
        return _cache[h]
    b64 = _b64(img)
    payload = {
        "model": QWEN_MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                {"type": "text", "text": "请识别这张图片中的所有文字，按原文顺序输出。只输出识别到的文字内容，不要解释。"}
            ]
        }],
        "max_tokens": 2000
    }
    headers = {"Authorization": f"Bearer {QWEN_API_KEY}", "Content-Type": "application/json"}
    try:
        r = httpx.post(QWEN_URL, json=payload, headers=headers, timeout=timeout)
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"].strip()
        _cache[h] = text
        if len(_cache) > 500:
            _cache.clear()
        return text
    except Exception as e:
        print(f"[qwen_ocr] API失败: {e}")
        return None

def ocr_with_fallback(img: Image.Image) -> str:
    """Qwen优先，失败回退本地"""
    t0 = time.time()
    # 1. Qwen
    text = ocr_qwen(img)
    if text:
        print(f"[qwen_ocr] Qwen识别成功 ({time.time()-t0:.1f}s, 缓存命中{_cache_hits}次)")
        return text
    # 2. 回退: 调用bot.py现有的本地OCR
    from deepseek_bot.bot import ocr_image
    return ocr_image(img)

# ========== 通用看图描述（qwen-vl-plus）==========
_client = None
def _get_client():
    global _client
    if _client is None:
        _client = httpx.Client(timeout=30, headers={"Authorization": f"Bearer {QWEN_API_KEY}", "Content-Type": "application/json"})
    return _client

def describe_image(img: Image.Image, prompt="详细描述这张图片的内容，包括人物、场景、动作、表情、穿着、文字等所有可见细节，用中文回答。", timeout=20) -> str:
    """通用视觉理解 - 什么都能看（人物/场景/表情包/截图/照片）"""
    global _cache_hits
    if not QWEN_API_KEY:
        return None
    h = _img_hash(img)
    if h in _cache:
        _cache_hits += 1
        return _cache[h]
    b64 = _b64(img)
    payload = {
        "model": QWEN_MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                {"type": "text", "text": prompt}
            ]
        }],
        "max_tokens": 1500
    }
    try:
        r = _get_client().post(QWEN_URL, json=payload, timeout=timeout)
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"].strip()
        _cache[h] = text
        if len(_cache) > 500:
            _cache.clear()
        return text
    except Exception as _e:
        print(f"[qwen_ocr] describe失败: {type(_e).__name__}: {_e}", flush=True)
        return None
