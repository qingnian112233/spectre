/** 与SPECTRE后端通信: 所有请求带 X-Init-Data(Telegram initData 或 ?init= 预览用) */

type TGWebApp = { initData?: string; ready?: () => void; expand?: () => void; setHeaderColor?: (c: string) => void; setBackgroundColor?: (c: string) => void; HapticFeedback?: any; colorScheme?: string }
declare global { interface Window { Telegram?: { WebApp?: TGWebApp } } }

export const TG = typeof window !== 'undefined' ? window.Telegram?.WebApp : undefined

/* 2026-09-22 老板「主屏幕打开不了」。
   从主屏幕图标 / 浏览器直接开时, Telegram 不注入 initData → INIT 为空 → 页面只剩
   一句"请从 Telegram 里打开"。所以 bot 的 /web new 会发一条带 ?init=<设备令牌> 的
   地址。有些浏览器把"添加到主屏幕"存成快捷方式时会丢掉 query → 这里再落一份
   localStorage 兜底, 以后不带参数也能开。TG 里打开永远优先 Telegram 自己那份。*/
const _AUTH_KEY = 'dsAuth'
const _qInit = (() => {
  try { return new URLSearchParams(location.search).get('init') || '' } catch { return '' }
})()
if (_qInit) { try { localStorage.setItem(_AUTH_KEY, _qInit) } catch { /* 无痕模式 */ } }
const _lsInit = (() => {
  try { return localStorage.getItem(_AUTH_KEY) || '' } catch { return '' }
})()

export const INIT: string = (TG && TG.initData) || _qInit || _lsInit

/** 手动贴令牌(页面上那个输入框): 存下来再刷新, 下次就不用手动了 */
export const setAuth = (v: string) => {
  try { localStorage.setItem(_AUTH_KEY, v.trim()) } catch { /* ignore */ }
  location.reload()
}

/** 当前用户(从 initData 里解出来): 名字 + Telegram 头像 */
export const ME: { id?: number; name?: string; photo?: string } = (() => {
  try {
    const u = JSON.parse(new URLSearchParams(INIT).get('user') || '{}')
    return { id: u.id, name: u.first_name || u.username || '', photo: u.photo_url || '' }
  } catch { return {} }
})()

/** SPECTRE的头像(老板发的那张, 放在 public/avatar.jpg) */
export const BOT_AVATAR = './avatar.jpg'

export type Task = { kind: 'sh' | 'dl'; uid: number; title: string; chat?: number; elapsed: number; line?: string; idle?: number }
export type Topic = { chat: number; topic: number; name: string; current: boolean; last: number | null }
export type Switches = { talk: boolean; typewriter: boolean; live: boolean; rich: boolean; bubble: boolean; react: boolean; delay: boolean; emoji: boolean; tools_show: boolean }
export type Watch = { id: string; name: string; kind: string; target: string; interval: number; enabled: boolean; runs: number; changes: number; last_ts: number; last_val: string }
export type FileItem = { name: string; size: number; mtime: number }
export type TodoItem = { text: string; state: 'todo' | 'doing' | 'done' }
/** 2026-10-05 网页补配置用 */
export type ChanInfo = { id: string; name: string; api: string; main: string; light: string; pro: string
                         keyMask: string; keyCount: number; models: string[] }
export type ChannelsResp = { ok: boolean; prov: string; model: string; light: string; pro: string
                             mode: string; think: string; channels: ChanInfo[] }
export type PromptSeg = { key: string; file: string; one: string; where: string; use: string
                          path: string; exists: boolean; chars: number; limit: number }
/** 2026-10-05 老板「子代理点不开吗? 就像图片这样可以看到, 然后ai互相交流也可以看到」 */
export type SubRow = { idx: number; task: string; role?: string; rnd: number; tools: number
                       done: boolean; fail?: boolean; el?: number; out?: string
                       key?: string; bk?: string; nLog?: number; nBoard?: number }
/** 子代理的一条消息链记录: r=角色(system/user/assistant/tool), t=正文(工具调用已压成一行) */
export type SubLogMsg = { r: string; t: string }
export type SubLog = { ok: boolean; task: string; role: string; st: string; el: number; rnd: number
                       tools: number; out: string; bk: string; log: SubLogMsg[]
                       board: { from: string; text: string; ts: number }[] }
/** 最近跑过的子代理(持久列表, 用于"跑完之后重新打开") */
export type SubItem = { key: string; idx: number; task: string; role: string; tool: string
                        st: string; el: number; rnd: number; tools: number
                        nLog: number; nBoard: number; ts: number; outLen: number }
/** 自动模式: 现在挂在哪几个会话上、谁在驱动、卡片是哪条、持久目标什么状态 */
export type AutoItem = { chat: string; drives: Record<string, number>; cardMid: number; closed: boolean
                         goal: { status: string; round: number; max: number; obj: string; note: string; t: number } }
export type State = {
  uid: number; name: string; ts: number; tasks: Task[]; topics: Topic[]; switches: Switches; watch: Watch[]
  model: { mode?: string; think?: string; light?: string; pro?: string; beta?: string }
  quota: { balance?: number; today?: number; tokens?: number; last_pay?: any }
  /** 2026-09-23: DeepSeek 账户余额(管理员才有; 非管理员 text 为空) */
  apikey?: { ok: boolean; text: string }
  mood: string; goal: string; todo: { items: TodoItem[]; alive?: boolean }; queue: number; files: FileItem[]
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, { ...init, headers: { 'X-Init-Data': INIT, ...(init?.headers || {}) } })
  if (!r.ok) {
    let d = ''
    try { d = (await r.json()).detail || '' } catch { /* ignore */ }
    const msg = d || (r.status === 401 ? '需要在 Telegram 里打开(initData 无效)' : r.status === 403 ? '仅管理员可用' : 'HTTP ' + r.status)
    throw new Error(msg)
  }
  return (await r.json()) as T
}

export type Step = { rnd: number; think?: string; note?: string; from?: number; to?: number }
export type ToolEvent = { tool: string; args: string; status: 'run' | 'ok' | 'err'; ms?: number; out?: string; urls?: string[] }

export type Session = { id: string; title: string; created: number; n: number; cur: boolean; tg: boolean }

export const api = {
  state: (topic = 0) => req<State>('/api/state', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ topic }) }),
  sessions: () => req<{ ok: boolean; sessions: Session[] }>('/api/sessions'),
  chatNew: () => req<{ ok: boolean; session: string; sessions: Session[]; msgs: any[] }>('/api/chat/new', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }),
  chatClear: (session: string) => req<{ ok: boolean; cleared: number; sessions: Session[]; msgs: any[] }>('/api/chat/clear', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ session }) }),
  chatDel: (session: string) => req<{ ok: boolean; sessions: Session[]; session: string; msgs: any[] }>('/api/chat/del', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ session }) }),
  history: (session: string, limit = 80) => req<{ ok: boolean; msgs: { me: boolean; text: string; events?: ToolEvent[]; steps?: Step[] }[] }>(`/api/chat/history?session=${encodeURIComponent(session)}&limit=${limit}`),
  chat: (text: string, session: string, topic = 0, atts: { name: string; size: number; path: string }[] = []) =>
    req<{ ok: boolean; job: string; session: string }>('/api/chat', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text, session, topic, atts }) }),
  // 2026-09-21 老板「显示token消耗量」: 服务端按轮累加 usage(prompt+completion), tk_last 是最后一次调用的拆分。
  poll: (job: string) => req<{ status: string; answer: string; stream: string; think: string; round: number; events: ToolEvent[]; notes: { rnd: number; text: string }[]; steps: Step[]; ask: { q: string; opts: string[]; multi: boolean } | null; sub: SubRow[]; sub_tool?: string; tokens?: number; tk_last?: { p?: number; c?: number }; elapsed: number }>(`/api/chat/poll?job=${encodeURIComponent(job)}`),
  // 2026-09-20 「页面关了重新打开看不到他继续跑」: 服务端 /api/chat/active 早就存在, 前端一直没有它。
  active: (session: string) => req<{ ok: boolean; job: string; status?: string; round?: number; stream?: string; think?: string; events?: ToolEvent[]; steps?: Step[]; notes?: { rnd: number; text: string }[]; ask?: { q: string; opts: string[]; multi: boolean } | null; sub?: SubRow[]; sub_tool?: string; tokens?: number; tk_last?: { p?: number; c?: number }; elapsed?: number }>(`/api/chat/active?session=${encodeURIComponent(session)}`),
  // 2026-09-21 老板「加一个回退功能网页端」: 删掉最后一轮「我 + 它的回答」, 把原话回吐填进输入框。
  undo: (session: string, n = 1) =>
    req<{ ok: boolean; session: string; removed: number; back: string; msgs: { me: boolean; text: string; events?: ToolEvent[]; steps?: Step[] }[] }>('/api/chat/undo', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ session, n }) }),
  answer: (job: string, ans: string) => req<{ ok: boolean }>('/api/chat/answer', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ job, ans }) }),
  interject: (job: string, text: string) => req<{ ok: boolean; queued: number }>('/api/chat/interject', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ job, text }) }),
  stop: (job: string) => req<{ ok: boolean; killed: boolean }>('/api/chat/stop', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ job }) }),
  health: () => req<any>('/api/health'),
  stopTask: (uid: number, kind: string) =>
    req<{ ok: boolean; killed: boolean; tasks: Task[] }>('/api/task/stop', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ uid, kind }) }),
  setModel: (patch: { mode?: string; think?: string }) =>
    req<{ ok: boolean; model: any }>('/api/model', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(patch) }),
  // ===== 2026-10-05 网页补配置: 通道 / 三档模型 / 通道key / 提示词 =====
  channels: () => req<ChannelsResp>('/api/model/channels'),
  setProv: (patch: { prov?: string; model?: string; light?: string; pro?: string }) =>
    req<{ ok: boolean; prov: string; model: any }>('/api/model/prov', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(patch) }),
  setKey: (prov: string, key?: string, api?: string) =>
    req<{ ok: boolean; msg: string }>('/api/model/key', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ prov, key, api }) }),
  promptList: () => req<{ ok: boolean; segs: PromptSeg[]; usage: string[] }>('/api/prompt', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ op: 'list' }) }),
  promptGet: (key: string) => req<{ ok: boolean; key: string; text: string }>('/api/prompt', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ op: 'get', key }) }),
  promptPut: (key: string, text: string) => req<{ ok: boolean; msg: string; segs: PromptSeg[] }>('/api/prompt', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ op: 'put', key, text }) }),
  /** ★2026-10-05 点开一个子代理: 拿完整执行记录(消息链) + 队友黑板(AI 互相交流) */
  subLog: (key: string, idx: number) =>
    req<SubLog>('/api/sub/log', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ key, idx }) }),
  /** ★2026-10-05 老板「怎么重新打开那个？」: 最近跑过的子代理清单(按自己 uid 过滤, 跑完/刷新后仍能进) */
  subList: (n = 40) => req<{ ok: boolean; items: SubItem[]; total: number }>(`/api/sub/list?n=${n}`),
  subClear: () => req<{ ok: boolean; removed: number; items: SubItem[]; total: number }>('/api/sub/clear', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }),
  // ★2026-10-05 老板「妈的后台还看不到 我还删不了」: 自动模式以前只在 TG 有一张卡片, 网页看不到也停不掉
  autoState: () => req<{ ok: boolean; items: AutoItem[] }>('/api/auto/state'),
  autoStop: (chat?: string) => req<{ ok: boolean; stopped: number }>('/api/auto/stop', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ chat: chat || '' }) }),
  autoDel: (chat?: string) => req<{ ok: boolean; deleted: number }>('/api/auto/del', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ chat: chat || '' }) }),
  setSwitch: (name: string, value: boolean) =>
    req<{ ok: boolean; switches: Switches }>('/api/switch', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, value }) }),
  watch: (act: string, wid: string) =>
    req<{ ok: boolean; watch: Watch[] }>('/api/watch', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ act, wid }) }),
  topics: () => req<{ ok: boolean; topics: Topic[] }>('/api/topic/save', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}) }).catch(() => ({ ok: false, topics: [] as Topic[] })),
  saveTopic: (topic: number, name: string, chat?: number) =>
    req<{ ok: boolean; topics: Topic[] }>('/api/topic/save', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ topic, name, chat }) }),
  sendTopic: (chat: number, topic: number, text: string) =>
    req<{ ok: boolean }>('/api/topic/send', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ chat, topic, text }) }),
  upload: (f: globalThis.File) => {
    const fd = new FormData(); fd.append('file', f)
    return req<{ ok: boolean; name: string; size: number; path: string; files: FileItem[] }>('/api/upload', { method: 'POST', body: fd })
  },
  fileUrl: (name: string) => `/api/file?name=${encodeURIComponent(name)}&init=${encodeURIComponent(INIT)}`,
  // 2026-09-20 老板「为啥有一大把文件」→ 文件页要能删: name 为空 = 清空整个目录
  fileDel: (name?: string) =>
    req<{ ok: boolean; removed: number; files: FileItem[] }>('/api/file/del', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: name || '' }),
    }),
}

export const fmt = {
  sec(s: number) {
    s = Math.max(0, Math.round(s || 0))
    if (s < 60) return s + 's'
    const m = Math.floor(s / 60)
    if (m < 60) return m + 'm' + (s % 60 ? (s % 60) + 's' : '')
    return Math.floor(m / 60) + 'h' + (m % 60 ? (m % 60) + 'm' : '')
  },
  size(n: number) {
    const u = ['B', 'KB', 'MB', 'GB']; let i = 0; n = Number(n || 0)
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++ }
    return (i ? n.toFixed(1) : n) + u[i]
  },
  // 2026-09-21 token 消耗量: 直接读数字太糙, 上万就折成 k
  tok(n: number) {
    n = Math.max(0, Math.round(Number(n) || 0))
    if (n < 1000) return String(n)
    if (n < 1000000) return (n / 1000).toFixed(n < 10000 ? 1 : 0) + 'k'
    return (n / 1000000).toFixed(2) + 'M'
  },
  time(t: number) {
    if (!t) return '-'
    return new Date(t * 1000).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
  },
}
