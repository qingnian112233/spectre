"""结构化输出解析 v2: nmap, nuclei, sqlmap, ffuf, subfinder, httpx, whatweb, arjun, wafw00f"""
import json, re, xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Any


def parse_nmap(text: str) -> dict:
    """解析 nmap 文本输出，提取开放端口、服务和 Web URL"""
    result = {"ip": "", "hostname": "", "ports": [], "services": [], "os": "", "web_urls": []}

    # IP
    m = re.search(r'Nmap scan report for\s+(\S+)', text)
    if m:
        result["ip"] = m.group(1)

    # 端口
    port_pattern = re.compile(r'^(\d+)/(tcp|udp)\s+(\w+)\s+(\S+)', re.MULTILINE)
    for m in port_pattern.finditer(text):
        result["ports"].append({
            "port": int(m.group(1)),
            "proto": m.group(2),
            "state": m.group(3),
            "service": m.group(4)
        })

    # 服务版本
    svc_pattern = re.compile(r'^(\d+)/(tcp|udp)\s+\w+\s+\S+\s+(.+)', re.MULTILINE)
    for m in svc_pattern.finditer(text):
        result["services"].append({
            "port": int(m.group(1)),
            "details": m.group(2).strip()
        })

    # OS 探测
    os_m = re.search(r'OS details:\s*(.+)', text)
    if os_m:
        result["os"] = os_m.group(1).strip()

    result["port_count"] = len(result["ports"])

    # 提取 Web URL
    ip = result["ip"]
    web_ports_https = {443, 8443, 4443, 9443, 5443}
    web_ports_http = {80, 8080, 3000, 5000, 8000, 8081, 8888, 9090, 9000}

    for p in result["ports"]:
        if p.get("state") != "open":
            continue
        port = p["port"]
        if port in web_ports_https:
            result["web_urls"].append(f"https://{ip}:{port}")
        elif port in web_ports_http:
            result["web_urls"].append(f"http://{ip}:{port}")

    # 也通过服务详情识别 Web 服务
    for s in result["services"]:
        svc_lower = s.get("details", "").lower()
        port = s.get("port", 0)
        if any(w in svc_lower for w in ("http", "nginx", "apache", "iis", "tomcat",
                                          "jetty", "node.js", "flask", "gunicorn",
                                          "uwsgi", "caddy", "traefik")):
            scheme = "https" if any(w in svc_lower for w in ("ssl", "tls", "https")) else "http"
            url = f"{scheme}://{ip}:{port}" if port not in (80, 443) else f"{scheme}://{ip}"
            if url not in result["web_urls"]:
                result["web_urls"].append(url)

    return result


def parse_nuclei(text: str) -> dict:
    """解析 nuclei JSON 输出或文本输出"""
    results = {"vulnerabilities": [], "info": [], "stats": {}}

    # 尝试 JSON 解析
    try:
        data = json.loads(text)
        if isinstance(data, list):
            for item in data:
                entry = {
                    "template": item.get("template-id", item.get("template", "")),
                    "name": item.get("info", {}).get("name", "") if isinstance(item.get("info"), dict) else "",
                    "severity": item.get("info", {}).get("severity", "info") if isinstance(item.get("info"), dict) else "info",
                    "host": item.get("host", ""),
                    "matched": item.get("matched-at", ""),
                    "type": item.get("type", ""),
                }
                if entry["severity"] in ("critical", "high", "medium"):
                    results["vulnerabilities"].append(entry)
                else:
                    results["info"].append(entry)
            results["stats"] = {
                "total": len(data),
                "critical": sum(1 for d in data if (d.get("info", {}).get("severity") if isinstance(d.get("info"), dict) else "") == "critical"),
                "high": sum(1 for d in data if (d.get("info", {}).get("severity") if isinstance(d.get("info"), dict) else "") == "high"),
                "medium": sum(1 for d in data if (d.get("info", {}).get("severity") if isinstance(d.get("info"), dict) else "") == "medium"),
            }
            return results
    except:
        pass

    # 文本解析
    lines = text.split('\n')
    current = {}
    for line in lines:
        line = line.strip()
        if line.startswith('['):
            m = re.match(r'\[(\w+)\]\s*\[([^\]]+)\]\s*(.+)', line)
            if m:
                if current:
                    sev = current.get("severity", "info")
                    if sev in ("critical", "high", "medium"):
                        results["vulnerabilities"].append(current)
                    else:
                        results["info"].append(current)
                current = {
                    "severity": m.group(1).lower(),
                    "template": m.group(2),
                    "name": m.group(3).strip(),
                    "host": "",
                }
    if current:
        results["vulnerabilities"].append(current)

    results["stats"]["total"] = len(results["vulnerabilities"]) + len(results["info"])
    return results


def parse_sqlmap(text: str) -> dict:
    """解析 sqlmap 输出"""
    result = {"injectable": False, "params": [], "dbms": "", "techniques": [], "tables": []}

    if "is vulnerable" in text.lower() or "sql injection" in text.lower():
        result["injectable"] = True

    # 参数
    for m in re.finditer(r"Parameter:\s*['\"]?(\S+)['\"]?\s*\((\w+)\)", text):
        result["params"].append({"name": m.group(1), "type": m.group(2)})

    # DBMS
    dbms_m = re.search(r'back-end DBMS:\s*(\S+)', text)
    if dbms_m:
        result["dbms"] = dbms_m.group(1)

    # Technique
    tech_m = re.search(r'Type:\s*(.+)', text)
    if tech_m:
        result["techniques"] = [t.strip() for t in tech_m.group(1).split("and")]

    # Tables
    for m in re.finditer(r'\|\s+(\w+)\s+\|', text):
        if m.group(1) not in ('Table', 'Database', '------'):
            result["tables"].append(m.group(1))

    return result


def parse_ffuf(text: str) -> dict:
    """解析 ffuf/dirsearch 输出"""
    result = {"results": [], "count": 0}

    for line in text.split('\n'):
        # ffuf JSON 行
        try:
            j = json.loads(line.strip())
            if isinstance(j, dict) and "url" in j:
                result["results"].append({
                    "url": j.get("url", ""),
                    "status": j.get("status", 0),
                    "size": j.get("length", 0),
                    "words": j.get("words", 0),
                })
                continue
        except:
            pass

        # 普通格式: 状态码  大小  路径
        m = re.match(r'(\d{3})\s+(\d+)\s+(\S+)', line)
        if m:
            result["results"].append({
                "status": int(m.group(1)),
                "size": int(m.group(2)),
                "path": m.group(3),
            })

    result["count"] = len(result["results"])
    return result


def parse_subfinder(text: str) -> dict:
    """解析子域名发现工具输出"""
    result = {"subdomains": [], "count": 0}

    for line in text.split('\n'):
        line = line.strip()
        if line and not line.startswith('[') and not line.startswith('=') and '.' in line:
            if not any(c in line for c in (' ', ':', '<', '>')):
                result["subdomains"].append(line)

    result["count"] = len(result["subdomains"])
    return result


def parse_httpx(text: str) -> dict:
    """解析 httpx 输出"""
    result = {"urls": [], "status_codes": {}, "technologies": []}

    for line in text.split('\n'):
        line = line.strip()
        if not line or not line.startswith('http'):
            continue
        parts = line.split(' ')
        url = parts[0] if parts else ""
        status = 0
        techs = []

        for i, p in enumerate(parts):
            if p.startswith('[') and p.endswith(']'):
                try:
                    status = int(p.strip('[]'))
                except:
                    pass
            if p.startswith('[') and ']' in p and not p.strip('[]').isdigit():
                techs = p.strip('[]').split(',')

        result["urls"].append({"url": url, "status": status, "tech": techs})
        result["status_codes"][str(status)] = result["status_codes"].get(str(status), 0) + 1
        for t in techs:
            result["technologies"].append(t)

    result["count"] = len(result["urls"])
    result["technologies"] = list(set(result["technologies"]))
    return result


def parse_whatweb(text: str) -> dict:
    """解析 whatweb 输出，提取框架/CMS/技术栈"""
    result = {"frameworks": [], "cms": [], "servers": [], "details": []}

    # whatweb 输出示例:
    # https://example.com [200] Cookies[], Country[RESERVED][ZZ], HTML5, 
    #   HTTPServer[nginx/1.18.0], IP[192.168.1.1], Title[Home]

    for line in text.split('\n'):
        line = line.strip()
        if not line:
            continue

        # 提取 URL 后面 [] 中的所有标签
        # 格式: URL [code] Tag1[value], Tag2[value], ...
        brackets = re.findall(r'(\w[\w\s/-]*?)\[([^\]]*)\]', line)

        for tag, value in brackets:
            tag_lower = tag.strip().lower()

            # CMS/Framework
            cms_keywords = ["cms", "framework", "wordpress", "joomla", "drupal",
                           "laravel", "django", "rails", "spring", "asp.net", "mvc"]
            if tag_lower in ("cms", "framework") or any(k in tag_lower for k in cms_keywords):
                result["cms"].append(f"{tag}:{value}" if value else tag)
                if value:
                    result["frameworks"].append(value)

            # Server
            if tag_lower in ("httpserver", "server", "x-powered-by"):
                result["servers"].append(value)
                result["frameworks"].append(value.split('/')[0])

            # General tech
            if tag_lower in ("html5", "jquery", "bootstrap", "react", "angular",
                            "vue", "php", "python", "ruby", "node.js", "flask",
                            "express", "next.js", "nuxt", "gatsby", "svelte"):
                result["frameworks"].append(tag)

            result["details"].append({"tag": tag, "value": value})

    result["frameworks"] = list(set(result["frameworks"]))
    return result


def parse_arjun(text: str) -> dict:
    """解析 arjun 参数发现输出"""
    result = {"params": [], "url": "", "count": 0}

    # arjun 输出示例:
    # [✓] parameter detected: id
    # [✓] parameter detected: token (heuristic)

    for line in text.split('\n'):
        line = line.strip()

        # 检测到的参数
        m = re.search(r'parameter detected:\s*(\S+)', line)
        if m:
            result["params"].append({
                "name": m.group(1),
                "url": result.get("url", ""),
            })

        # 目标 URL
        url_m = re.search(r'Scanning\s+(\S+)', line)
        if url_m:
            result["url"] = url_m.group(1)

    result["count"] = len(result["params"])
    return result


def parse_wafw00f(text: str) -> dict:
    """解析 wafw00f 输出"""
    result = {"waf": "", "vendor": "", "detected": False}

    # wafw00f 输出示例:
    # The site https://example.com is behind Cloudflare (Cloudflare Inc.) WAF.

    m = re.search(r'is behind\s+(.+?)\s+WAF', text)
    if m:
        result["detected"] = True
        waf_full = m.group(1).strip()
        result["waf"] = waf_full
        # 提取 vendor
        vendor_m = re.search(r'\((.+?)\)', waf_full)
        if vendor_m:
            result["vendor"] = vendor_m.group(1)

    # 另一个常见格式
    if not result["detected"]:
        m2 = re.search(r'behind\s+(.+?)(?:\n|$|,)', text)
        if m2:
            result["detected"] = True
            result["waf"] = m2.group(1).strip()

    # No WAF
    if "No WAF" in text or "not behind" in text.lower():
        result["detected"] = False

    return result


def parse_auto_verify(text: str) -> dict:
    """解析 auto-verify JSON 输出"""
    result = {"verified": 0, "total": 0, "confirmed": [], "likely": [], "false_positive": [], "errors": []}
    try:
        data = json.loads(text)
        result["total"] = data.get("total", 0)
        result["verified"] = data.get("verified", 0)
        for r in data.get("results", []):
            entry = {
                "url": r.get("url", ""),
                "vuln_type": r.get("vuln_type", ""),
                "template": r.get("template", ""),
                "confidence": r.get("confidence", 0),
            }
            verdict = r.get("verdict", "")
            if verdict in ("confirmed",):
                result["confirmed"].append(entry)
            elif verdict in ("likely",):
                result["likely"].append(entry)
            elif verdict in ("false_positive", "negative"):
                result["false_positive"].append(entry)
            elif "error" in r:
                result["errors"].append(r)
        return result
    except (json.JSONDecodeError, TypeError):
        pass
    return result


def auto_parse(tool: str, text: str) -> dict:
    """自动选择解析器"""
    parsers = {
        "nmap": parse_nmap,
        "nuclei": parse_nuclei,
        "nuclei_ports": parse_nuclei,
        "nuclei_targeted": parse_nuclei,
        "sqlmap": parse_sqlmap,
        "ffuf": parse_ffuf,
        "gobuster": parse_ffuf,
        "dirsearch": parse_ffuf,
        "feroxbuster": parse_ffuf,
        "subfinder": parse_subfinder,
        "theharvester": parse_subfinder,
        "amass": parse_subfinder,
        "httpx": parse_httpx,
        "whatweb": parse_whatweb,
        "arjun": parse_arjun,
        "wafw00f": parse_wafw00f,
        "auto-verify": parse_auto_verify,
        "auto_verify": parse_auto_verify,
    }
    parser = parsers.get(tool.lower())
    if parser:
        try:
            return parser(text)
        except:
            pass
    return {"raw": text[:500]}
