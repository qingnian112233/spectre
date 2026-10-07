"""
Captcha Solver - CapMonster integration
Usage:
    from captcha_solver import solve_text, solve_recaptcha, solve_hcaptcha, solve_slide

    # 普通图文验证码
    result = solve_text("/path/to/captcha.png")

    # reCAPTCHA v2/v3
    token = solve_recaptcha("https://target.com", "6Ld...sitekey")

    # hCaptcha
    token = solve_hcaptcha("https://target.com", "sitekey")

    # FunCaptcha (Arkose Labs)
    token = solve_funcaptcha("https://target.com", "public_key")

    # Cloudflare Turnstile
    token = solve_turnstile("https://target.com", "sitekey")

    # 滑块验证码坐标
    result = solve_slide("slide.png", "background.png")
"""

import base64
import time
from capmonster_python import (
    ImageToTextTask,
    RecaptchaV2Task,
    RecaptchaV3Task,
    FunCaptchaTask,
    TurnstileTask,
    CapmonsterClient,
)

# API Key
API_KEY = "2abe6d06b6cd6e8786852b9b2ce1ecd7"


def solve_text(image_path: str, module_name: str = "amazon") -> str:
    """
    普通图文验证码识别
    Args:
        image_path: 验证码图片路径
        module_name: OCR模块名 (amazon, google, etc.)
    Returns:
        识别结果字符串, 失败返回 None
    """
    try:
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()

        capmonster = ImageToTextTask(API_KEY)
        task_id = capmonster.create_task(
            image_string=img_b64,
            module_name=module_name,
            recognizing_threshold=95,
        )
        result = capmonster.join_task(task_id)
        return result.get("text", {}).get("value", "")
    except Exception as e:
        print(f"[Captcha] Text solve error: {e}")
        return None


def solve_text_bytes(img_bytes: bytes, module_name: str = "amazon") -> str:
    """从 bytes 识别图文验证码"""
    try:
        img_b64 = base64.b64encode(img_bytes).decode()
        capmonster = ImageToTextTask(API_KEY)
        task_id = capmonster.create_task(
            image_string=img_b64,
            module_name=module_name,
            recognizing_threshold=95,
        )
        result = capmonster.join_task(task_id)
        return result.get("text", {}).get("value", "")
    except Exception as e:
        print(f"[Captcha] Text solve error: {e}")
        return None


def solve_recaptcha(url: str, sitekey: str, invisible: bool = False) -> str:
    """
    reCAPTCHA v2 识别
    Args:
        url: 目标页面URL
        sitekey: Google sitekey
        invisible: 是否不可见 recaptcha
    Returns:
        g-recaptcha-response token
    """
    try:
        capmonster = RecaptchaV2Task(API_KEY)
        task_id = capmonster.create_task(
            website_url=url,
            website_key=sitekey,
            is_invisible=invisible,
        )
        result = capmonster.join_task(task_id)
        return result.get("gRecaptchaResponse", "")
    except Exception as e:
        print(f"[Captcha] reCAPTCHA solve error: {e}")
        return None


def solve_recaptcha_v3(url: str, sitekey: str, min_score: float = 0.3) -> str:
    """
    reCAPTCHA v3 识别
    Args:
        url: 目标页面URL
        sitekey: Google sitekey
        min_score: 最低分数 (0.1-0.9)
    Returns:
        g-recaptcha-response token
    """
    try:
        capmonster = RecaptchaV3Task(API_KEY)
        task_id = capmonster.create_task(
            website_url=url,
            website_key=sitekey,
            min_score=min_score,
        )
        result = capmonster.join_task(task_id)
        return result.get("gRecaptchaResponse", "")
    except Exception as e:
        print(f"[Captcha] reCAPTCHA v3 solve error: {e}")
        return None


def solve_hcaptcha(url: str, sitekey: str) -> str:
    """
    hCaptcha 识别
    Returns:
        h-captcha-response token
    """
    try:
        client = CapmonsterClient(API_KEY)
        task_id = client.create_task({
            "type": "HCaptchaTaskProxyless",
            "websiteURL": url,
            "websiteKey": sitekey,
        })
        result = client.join_task_result(task_id)
        return result.get("gRecaptchaResponse", "")
    except Exception as e:
        print(f"[Captcha] hCaptcha solve error: {e}")
        return None


def solve_funcaptcha(url: str, public_key: str, subdomain: str = "") -> str:
    """
    FunCaptcha (Arkose Labs) 识别
    Returns:
        funcaptcha token
    """
    try:
        capmonster = FunCaptchaTask(API_KEY)
        task_id = capmonster.create_task(
            website_url=url,
            website_public_key=public_key,
            funcaptcha_api_js_subdomain=subdomain if subdomain else None,
        )
        result = capmonster.join_task(task_id)
        return result.get("token", "")
    except Exception as e:
        print(f"[Captcha] FunCaptcha solve error: {e}")
        return None


def solve_turnstile(url: str, sitekey: str) -> str:
    """
    Cloudflare Turnstile 识别
    Returns:
        cf-turnstile-response token
    """
    try:
        capmonster = TurnstileTask(API_KEY)
        task_id = capmonster.create_task(
            website_url=url,
            website_key=sitekey,
        )
        result = capmonster.join_task(task_id)
        return result.get("token", "")
    except Exception as e:
        print(f"[Captcha] Turnstile solve error: {e}")
        return None


def solve_slide(slide_path: str, bg_path: str) -> dict:
    """
    滑块验证码 - 返回滑动坐标
    NOTE: CapMonster 不直接支持滑块距离识别
    这里用本地 ddddocr 做兜底
    Returns:
        {'x': sliding_distance, 'target': [x, y, x2, y2]}
    """
    try:
        import ddddocr
        det = ddddocr.DdddOcr(det=True)
        with open(slide_path, "rb") as f:
            slide = f.read()
        with open(bg_path, "rb") as f:
            bg = f.read()
        result = det.slide_match(slide, bg)
        return {"x": result.get("target", [0])[0] if result else 0, "target": result.get("target", [])}
    except ImportError:
        print("[Captcha] ddddocr not installed, cannot solve slide")
        return {"x": 0, "target": []}
    except Exception as e:
        print(f"[Captcha] Slide solve error: {e}")
        return {"x": 0, "target": []}


def get_balance() -> float:
    """查询账户余额"""
    try:
        from capmonster_python import CapmonsterClient
        import requests
        resp = requests.post("https://api.capmonster.cloud/getBalance", json={
            "clientKey": API_KEY
        })
        return resp.json().get("balance", 0)
    except Exception as e:
        print(f"[Captcha] Balance check error: {e}")
        return -1


if __name__ == "__main__":
    print(f"💰 余额: ${get_balance():.4f}")
    print("✅ CapMonster 已就绪")
