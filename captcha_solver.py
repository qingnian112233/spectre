"""CapMonster 打码模块"""
import requests
import base64
import json
import os
import time

API_KEY = "2abe6d06b6cd6e8786852b9b2ce1ecd7"
BASE_URL = "https://api.capmonster.cloud"

def get_balance():
    r = requests.post(f"{BASE_URL}/getBalance", json={"clientKey": API_KEY}, timeout=10)
    data = r.json()
    if data.get("errorId") == 0:
        return data["balance"]
    raise Exception(f"查余额失败: {data}")

def create_task(task_data):
    task_data["clientKey"] = API_KEY
    r = requests.post(f"{BASE_URL}/createTask", json=task_data, timeout=10)
    data = r.json()
    if data.get("errorId") != 0:
        raise Exception(f"创建任务失败: {data}")
    return data["taskId"]

def wait_result(task_id, max_wait=120):
    for _ in range(max_wait):
        r = requests.post(f"{BASE_URL}/getTaskResult",
                         json={"clientKey": API_KEY, "taskId": task_id}, timeout=10)
        data = r.json()
        if data.get("errorId") != 0:
            raise Exception(f"获取结果失败: {data}")
        if data["status"] == "ready":
            return data["solution"]
        time.sleep(1)
    raise Exception("超时")

def solve_text(image_path):
    """普通图文验证码"""
    with open(image_path, "rb") as f:
        body = base64.b64encode(f.read()).decode()
    task_id = create_task({
        "task": {"type": "ImageToTextTask", "body": body}
    })
    sol = wait_result(task_id)
    return sol["text"]

def solve_recaptcha(url, sitekey, invisible=False):
    """reCAPTCHA v2"""
    task_id = create_task({
        "task": {
            "type": "RecaptchaV2TaskProxyless" if not invisible else "RecaptchaV2TaskProxyless",
            "websiteURL": url,
            "websiteKey": sitekey,
            "isInvisible": invisible
        }
    })
    sol = wait_result(task_id)
    return sol["gRecaptchaResponse"]

def solve_recaptcha_v3(url, sitekey, min_score=0.3, action="verify"):
    """reCAPTCHA v3"""
    task_id = create_task({
        "task": {
            "type": "RecaptchaV3TaskProxyless",
            "websiteURL": url,
            "websiteKey": sitekey,
            "minScore": min_score,
            "pageAction": action
        }
    })
    sol = wait_result(task_id)
    return sol["gRecaptchaResponse"]

def solve_hcaptcha(url, sitekey):
    """hCaptcha"""
    task_id = create_task({
        "task": {
            "type": "HCaptchaTaskProxyless",
            "websiteURL": url,
            "websiteKey": sitekey
        }
    })
    sol = wait_result(task_id)
    return sol["gRecaptchaResponse"]

def solve_funcaptcha(url, sitekey, subdomain=""):
    """FunCaptcha / Arkose"""
    task_id = create_task({
        "task": {
            "type": "FunCaptchaTaskProxyless",
            "websiteURL": url,
            "funcaptchaApiJSSubdomain": subdomain,
            "websitePublicKey": sitekey
        }
    })
    sol = wait_result(task_id)
    return sol["token"]

def solve_turnstile(url, sitekey):
    """Cloudflare Turnstile"""
    task_id = create_task({
        "task": {
            "type": "TurnstileTaskProxyless",
            "websiteURL": url,
            "websiteKey": sitekey
        }
    })
    sol = wait_result(task_id)
    return sol["token"]

def solve_slide(slide_path, bg_path):
    """滑块验证码 - 用 ddddocr 本地识别"""
    import ddddocr
    det = ddddocr.DdddOcr(det=True)
    with open(slide_path, "rb") as f:
        slide = f.read()
    with open(bg_path, "rb") as f:
        bg = f.read()
    return det.slide_match(slide, bg)


if __name__ == "__main__":
    import sys
    act = sys.argv[1] if len(sys.argv) > 1 else ""
    if act == "balance":
        print(json.dumps({"balance": get_balance()}))
    elif act == "text":
        print(solve_text(sys.argv[2]))
    elif act == "recaptcha":
        print(solve_recaptcha(sys.argv[2], sys.argv[3]))
    elif act == "recaptcha_v3":
        print(solve_recaptcha_v3(sys.argv[2], sys.argv[3], float(sys.argv[4]) if len(sys.argv) > 4 else 0.3))
    elif act == "hcaptcha":
        print(solve_hcaptcha(sys.argv[2], sys.argv[3]))
    elif act == "funcaptcha":
        print(solve_funcaptcha(sys.argv[2], sys.argv[3]))
    elif act == "turnstile":
        print(solve_turnstile(sys.argv[2], sys.argv[3]))
    elif act == "slide":
        print(json.dumps(solve_slide(sys.argv[2], sys.argv[3])))
    else:
        print("Usage: captcha_solver.py <act> [args]")
