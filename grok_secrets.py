"""
xAI Grok Build 密钥脱敏模块 (Rust → Python 移植)
来源: github.com/xai-org/grok-build/crates/codegen/xai-grok-secrets
"""

import re
import json
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse

REDACTED = "[REDACTED_SECRET]"
REDACTED_URL_VALUE = "redacted"

# ── 11 类密钥正则 (直接来自 Grok Build sanitizer.rs) ──

# sk- / sk_ / xai- 前缀的 API Key
API_KEY_PREFIX_RE = re.compile(r"\b(?:sk[-_]|xai-)[A-Za-z0-9_-]{20,}")

# AWS Access Key ID: AKIA... / ASIA...
AWS_ACCESS_KEY_RE = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")

# GitHub PAT: ghp_ / gho_ / ghu_ / ghs_ / ghr_ + github_pat_
GITHUB_TOKEN_RE = re.compile(
    r"\b(?:gh[opusr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"
)

# GitLab + Slack: glpat- / xoxa- / xoxb- / xoxp- / xapp-
VENDOR_TOKEN_RE = re.compile(r"\b(?:glpat-|xox[abp]-|xapp-)[A-Za-z0-9-]{10,}")

# Google API Key: AIza + 35 chars
GOOGLE_API_KEY_RE = re.compile(r"\bAIza[0-9A-Za-z_-]{35}")

# PEM 私钥块
PEM_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)

# Bearer Token
BEARER_TOKEN_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{16,}\b", re.IGNORECASE)

# 裸 JWT: eyJ...
JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")

# 赋值型密钥: api_key=xxx / token: xxx / secret="xxx"
SECRET_ASSIGNMENT_RE = re.compile(
    r"""
    \b(
        api[_-]?key
      | (?:access|refresh|id)[_-]token
      | token
      | secret
      | client[_-]secret
      | password
    )\b
    (\s*[:=]\s*)
    (["']?)
    [^\s"',&]{8,}
    """,
    re.IGNORECASE | re.VERBOSE,
)

# URL 匹配 (排除标点防止误吞)
URL_RE = re.compile(r'https?://[^\s"\'<>(){}\[\],;`]+')

# 敏感 URL 查询参数
SENSITIVE_QUERY_PARAMS = {
    "access_token", "api_key", "assertion", "auth", "client_secret",
    "code", "code_verifier", "id_token", "key", "password",
    "refresh_token", "requested_token", "session_id", "state",
    "subject_token", "token",
}

# ── 密钥正则列表 (按优先级排列) ──
_SECRET_REGEXES = [
    API_KEY_PREFIX_RE,
    AWS_ACCESS_KEY_RE,
    GITHUB_TOKEN_RE,
    VENDOR_TOKEN_RE,
    GOOGLE_API_KEY_RE,
    PEM_PRIVATE_KEY_RE,
    BEARER_TOKEN_RE,
    JWT_RE,
    SECRET_ASSIGNMENT_RE,
]


def redact_secrets(text: str) -> str:
    """扫描文本中所有已知密钥格式，替换为 [REDACTED_SECRET]"""
    # 白名单: tg://proxy 代理链接的 secret 参数是用户要用的，不能脱敏
    if "tg://proxy" in text:
        return text
    for regex in _SECRET_REGEXES:
        text = regex.sub(REDACTED, text)
    return text


def redact_url(url: str) -> str:
    """脱敏 URL 中的敏感查询参数值"""
    try:
        parsed = urlparse(url)
        if not parsed.query:
            return url

        params = parse_qs(parsed.query, keep_blank_values=True)
        redacted_params = {}
        for key, values in params.items():
            if key.lower() in SENSITIVE_QUERY_PARAMS:
                redacted_params[key] = [REDACTED_URL_VALUE]
            else:
                redacted_params[key] = values

        new_query = urlencode(redacted_params, doseq=True)
        return urlunparse(parsed._replace(query=new_query))
    except Exception:
        return url


def redact_user_paths(text: str, username: str = None) -> str:
    """脱敏 /home/user 和 /Users/user 路径"""
    import os
    home = os.path.expanduser("~")
    if username:
        user_home = f"/home/{username}"
        text = text.replace(user_home, "/home/[REDACTED_USER]")
        text = re.sub(
            rf"\b/home/{re.escape(username)}\b",
            "/home/[REDACTED_USER]",
            text,
        )
    # 通用 home 目录
    text = text.replace(home, "[REDACTED_HOME]")
    return text


def walk_json_strings(json_str: str) -> str:
    """遍历 JSON 中所有字符串值并脱敏"""
    try:
        obj = json.loads(json_str)
        redacted = _redact_json_obj(obj)
        return json.dumps(redacted)
    except json.JSONDecodeError:
        return json_str


def _redact_json_obj(obj):
    """递归脱敏 JSON 对象中的字符串值"""
    if isinstance(obj, str):
        return redact_secrets(obj)
    elif isinstance(obj, dict):
        return {k: _redact_json_obj(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_redact_json_obj(item) for item in obj]
    return obj


def redact_json_string_values(json_str: str) -> str:
    """walk_json_strings 的别名 (兼容 Rust API)"""
    return walk_json_strings(json_str)


def has_secrets(text: str) -> bool:
    """检测文本是否包含疑似密钥 (不做替换)"""
    for regex in _SECRET_REGEXES:
        if regex.search(text):
            return True
    return False


def find_secrets(text: str) -> list[dict]:
    """返回所有检测到的密钥信息 (类型 + 位置)"""
    results = []
    patterns = [
        ("api_key_prefix", API_KEY_PREFIX_RE),
        ("aws_access_key", AWS_ACCESS_KEY_RE),
        ("github_token", GITHUB_TOKEN_RE),
        ("vendor_token", VENDOR_TOKEN_RE),
        ("google_api_key", GOOGLE_API_KEY_RE),
        ("pem_private_key", PEM_PRIVATE_KEY_RE),
        ("bearer_token", BEARER_TOKEN_RE),
        ("jwt", JWT_RE),
        ("secret_assignment", SECRET_ASSIGNMENT_RE),
    ]
    for name, regex in patterns:
        for m in regex.finditer(text):
            results.append({
                "type": name,
                "start": m.start(),
                "end": m.end(),
                "match_preview": m.group()[:40] + ("..." if len(m.group()) > 40 else ""),
            })
    return results


# ── 快捷函数: 一行脱敏 ──

def clean(text: str) -> str:
    """全量清洗: 密钥 + URL参数 + 用户路径"""
    text = redact_secrets(text)
    text = redact_url(text)
    text = redact_user_paths(text)
    return text
