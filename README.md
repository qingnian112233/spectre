# SPECTRE · 多智能体 Telegram 框架

[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.10%2B-3776ab.svg)
![Platform](https://img.shields.io/badge/Platform-Telegram-26a5e4.svg)

跑在 **Telegram** 上的自主智能体引擎：多 AI 编排、可扩展工具链、自进化知识库，外加一个完整的 Web 控制台（Mini App）。
作者 / 联系：**@eexse**

---

## 这是什么（以及不是什么）

这是一个**通用智能体框架**：多 AI 编排、工具层、记忆、知识库、Telegram 交互、Web 控制台。

它只包含**通用智能体框架本身**：编排、工具层、记忆、知识库、Telegram 交互与 Web 控制台。
上游自用版本里的引擎扩展与提示词层**不在本仓库范围内**。
这些在上游自用版本里存在，但不在本仓库范围内 —— 本项目只提供**编排与执行框架本身**，
所有额外能力通过声明式插件（`knowledge/toolPlugins/`）自行扩展。
代码里保留了 `_OSS_DISABLED` 白名单与 `rt()` 守卫：即使模型拿到那些旧工具名，也只会得到一句「该能力不在开源框架版」，不会报错。

请只在你**拥有合法授权**的环境中使用。

---

## 特性

**多 AI 编排**
- `team` 多角色并行 + 实时进度面板
- `workflow` 通用编排：任务清单 fan-out、多阶段流水线（上一阶段结果喂下一阶段）、并发可控
- `subagent` 独立上下文子代理（并行）、`ralph` 全新上下文迭代（长任务不跑偏）、`goal` 持久目标自动续跑
- **队友黑板**：同一批代理共享一块黑板，可 `SAY:` 广播发现、互相提示、共享"已排除的向量"，不重复踩坑

**工具层**
- 60+ 内置工具：`sh` `read` `write` `edit` `url` `search` `file` `img` `pdf` `captcha` `notify`
  `watch` `todo` `memory` `group_memory` `project` `report` `schedule` `parse` `coin` `shot` …
- 声明式插件：往 `knowledge/toolPlugins/` 丢一个 JSON + 脚本即可扩展工具，**不用改核心**

**自进化**
- `atk_meta`：把成功的执行链蒸馏成可迁移"成功路径"入库、下次自动回灌
- 知识库 TRIGGER 机制：按关键词自动注入相关手册（文件头写 `<!-- TRIGGER: 关键词 -->`）
- 记忆引擎：跨会话长期记忆 + 群聊画像 + 状态固化（断点续跑）

**Telegram 侧体验**
- 流式输出 / 打字机 / 心跳 / 任务清单面板 / 值守 / 定时任务 / 群管 / 多模态（看图、OCR、PDF、语音）
- 富文本：真表格、自定义动画表情、动态时间实体
- 私聊话题（Bot API 9.4）：一个私聊可开多个话题，一个话题就是一个独立工作台

**Web 控制台（Mini App）**
- 对话（实时流式 + token 计量 + 子代理层） / 概览 / 任务 / 工作台 / 值守 / 文件 / 设置
- 前后端同进程，自带 HTTPS 一键脚本（`miniapp-src/setup_https.sh`）

---

## 架构

```
deepseek_bot/          框架主程序
  bot.py               核心（工具 schema / 执行器 / 提示词分层 / 心跳 / 面板 / 回调）
  miniapp_server.py    Web 控制台后端（FastAPI）
  rich_msg.py          富文本（Telegram Rich Message：表格 / 自定义表情 / 实体）
  atk_meta.py          自进化（执行链蒸馏 / 回灌）
  memory_engine.py     长期记忆与检索
  group_memory.py      群聊画像
  state_engine.py      状态固化（断点续跑）
  parallel.py          多 AI 编排（team / workflow / subagent）
  rag_engine.py        知识库检索
  parser.py            20+ 工具输出解析
  reporter.py          报告生成（md / pdf）
  scheduler.py         定时任务
  watchdog.py          进程守护
  tools_schema.py      工具定义表
  prompts.py           可热改提示词注册表
  db.py                数据层
defense/               注入识别与防护
knowledge/             知识库（toolPlugins 声明式插件 / self_learned 自沉淀 / suggestions 建议）
miniapp/               Web 控制台前端（构建产物，已就绪）
miniapp-src/           前端源码（Vite + React + Tailwind，含自研 dsh 设计系统）
assistant_api.py       OpenAI 兼容 API 服务（可选）
tg_user.py             多账号操作脚本
docs/                  使用手册 / 功能与测试指南 / 测试提示词
```

---

## 安装

```bash
git clone https://github.com/<你的用户名>/spectre.git && cd spectre
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env      # 至少填 DEEPSEEK_API_KEY 与 DEEPSEEK_BOT_TOKEN
mkdir -p sessions && .venv/bin/python -m deepseek_bot.run
```

### ⚠️ 部署路径

代码里默认的部署目录是 **`/opt/deepseek-bot`**（知识库、session、日志、插件的绝对路径都指向它）。
装到别处就全局替换一次：

```bash
sed -i 's|/opt/deepseek-bot|/your/actual/path|g' deepseek_bot/*.py *.py knowledge/toolPlugins/*.py
```

### 配置（.env）

| 变量 | 说明 |
|---|---|
| `DEEPSEEK_API_KEY` | 模型 API key（必填） |
| `DEEPSEEK_API_BASE` / `DEEPSEEK_API_BK` | API 端点（主 / 备用，主通道故障自动切换） |
| `DEEPSEEK_BOT_TOKEN` | Telegram bot token（必填） |
| `TG_API_ID` / `TG_API_HASH` | Telegram 应用凭据（多号功能需要） |
| `FOFA_API_KEY` / `FOFA_EMAIL` | 资产测绘（可选） |
| `QWEN_API_KEY` | 向量 / 多模态（可选） |
| `PROXY_DYNAMIC_URL` | 动态代理池（留空 = 直连） |
| `DSB_BRAND` | 显示名（默认 `SPECTRE`，也可用 `/brand` 命令改） |
| `DSB_WEB_URL` | Web 控制台公网地址（默认 `https://YOUR_HOST.sslip.io`，**要改成你自己的**） |

---

## ⚠️ 必改（5 分钟）

开源版把真实 TG ID 换成了 `None` 占位、把服务器地址换成了 `YOUR_HOST`。
**不改成你自己的数字 ID 就没有管理员**（`None` 永不等于任何 chat_id，所以也不会把管理权误给陌生人）：

```python
deepseek_bot/bot.py    OK = {None}          → OK = {你的TG_ID}
assistant_api.py       _ADMINS = {None}     → _ADMINS = {你的TG_ID}
tg_user.py             ACCOUNTS 里的 "tg_id": 0 → 你的双号 TG ID
deepseek_bot/miniapp_server.py   PUBLIC_URL  → 你的域名
.env                   DSB_WEB_URL          → 你的域名
```

查自己的 ID：随便找个 userinfo bot，或看机器人启动日志里的 `chat_id`。

---

## 定制点

| 要改什么 | 改哪里 |
|---|---|
| 管理员名单（必改） | `bot.py` 顶部 `OK = {...}` |
| 人格 / 语气 | `bot.py` 里的 `_OSS_SELF`（自身身份 / 性格 / 表达）与 `_TONE_SYS` |
| 模型档位 | `_PROV` 常量 + `/model` 面板（管理员可在面板里直接改 key 与入口） |
| 工具能力 | 优先加 `knowledge/toolPlugins/*.json`（声明式，免改核心） |
| 可热改提示词 | `/prompt` 面板（注册表在 `deepseek_bot/prompts.py`） |
| Web 控制台 | `miniapp-src/`（`npm run build` 后把 `dist/` 拷到 `miniapp/`） |

---

## 文档

- [使用手册](docs/SPECTRE使用手册.md)
- [功能与测试指南](docs/SPECTRE功能与测试指南.md)
- [测试提示词](docs/SPECTRE测试提示词.md)

---

## 使用边界

本项目用于**授权的**安全测试、红队演练与学术研究。使用者必须确保对目标拥有合法授权，并自行承担合规责任；
作者不对任何未授权使用负责。

---

## License

[AGPL-3.0](LICENSE) —— 可自由使用、修改、分发；**若作为网络服务对外提供，必须公开你修改后的完整源码**。

Copyright (C) 2026 青念 (@eexse)
