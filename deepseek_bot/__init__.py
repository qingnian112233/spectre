"""SPECTRE · 多智能体 Telegram 框架

框架层与工具层在这里收口; 进攻性引擎模块不在开源范围内(见仓库 README)。

模块导览:
  核心:
    bot                  主程序(工具 schema / 执行器 / 提示词分层 / 心跳 / 面板 / 回调)
    miniapp_server       Web 控制台后端(FastAPI)
    rich_msg             富文本(Telegram Rich Message: 表格 / 自定义表情 / 实体)
    tools_schema         工具定义表
    prompts              可热改提示词注册表
    run                  启动入口

  编排:
    parallel             team / workflow / subagent 并发编排
    atk_meta             自进化(执行链蒸馏 / 回灌)

  记忆与状态:
    memory_engine        长期记忆与检索
    group_memory         群聊画像
    state_engine         状态固化(断点续跑)
    scan_cache           扫描结果缓存
"""

from .parallel import ParallelScheduler, batch_from_project_ids, TargetJob
from .reporter import generate_md, generate_pdf, generate_summary, export_project
from .parser import auto_parse
from .scheduler import start_scheduler
from .rich_msg import try_send_rich, send_rich_html
from .state_engine import init as state_init, load as state_load, snapshot as state_snapshot
from .memory_engine import (
    retrieve_context, auto_extract_facts, should_summarize, mark_summary_done,
    update_summary, add_fact, get_memory_stats, search_conversations, search_all_users,
    get_recent_topics, set_pref, get_pref, get_all_prefs, load_memories, check_pending_summary,
)
from .group_memory import (
    record_message as grp_record, retrieve_group_context as grp_context,
    get_group_stats as grp_stats, auto_summarize_group as grp_summarize,
    clear_group_messages as grp_clear_messages,
)
from .group_mod import active_bans as gmb_active_bans
from .db import init as db_init

__all__ = [
    "ParallelScheduler", "TargetJob", "batch_from_project_ids",
    "generate_md", "generate_pdf", "generate_summary", "export_project",
    "auto_parse", "start_scheduler", "try_send_rich", "send_rich_html",
    "state_init", "state_load", "state_snapshot",
    "retrieve_context", "auto_extract_facts", "add_fact", "get_memory_stats",
    "grp_record", "grp_context", "grp_stats",
    "db_init",
]
