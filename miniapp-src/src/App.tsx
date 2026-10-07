/** 外壳: 左侧栏(桌面) / 抽屉(手机) + 主题切换 + 状态轮询 */
import * as React from 'react'
import { Home, ListTodo, MessageSquare, Folder, Eye, SlidersHorizontal, FolderOpen, CreditCard, Maximize2, Minimize2 } from 'lucide-react'
import { api, INIT, TG, BOT_AVATAR, setAuth, type State } from './api'
import { Btn, Card, Spinner, cx } from './ui'
import { Overview, Tasks, Chat, Topics, Watch, Settings, Files, Billing } from './views'
import { M, I } from './morph'
import { AgentNav, type AgentRef } from './nav'
import { AgentPage, SubListPage } from './agent'

// 编译时刻(vite.config.ts 里 define 注入)。typeof 兜底 → 万一定义没注入也只是显示 dev, 不会白屏。
declare const __BUILD__: string
const BUILD = typeof __BUILD__ === 'string' ? __BUILD__ : 'dev'

// 2026-09-20 老板「把对话放在最前面 不然重新打开小程序老是得重新找对话麻烦」→ 对话排第一
const TABS = [
  { k: 'chat', n: '对话', I: MessageSquare },
  { k: 'overview', n: '概览', I: Home },
  { k: 'tasks', n: '任务', I: ListTodo },
  { k: 'topics', n: '工作台', I: Folder },
  { k: 'watch', n: '值守', I: Eye },
  { k: 'files', n: '文件', I: FolderOpen },
  { k: 'settings', n: '设置', I: SlidersHorizontal },
  { k: 'billing', n: '账单', I: CreditCard },
] as const
type TabKey = typeof TABS[number]['k']

export default function App() {
  // 2026-09-20: 默认落在「对话」, 并记住上次在哪一页(?tab= 优先, 其次本地记忆, 最后对话)
  const [tab, setTab] = React.useState<TabKey>(() => {
    const q = new URLSearchParams(location.search).get('tab') as TabKey | null
    if (q && TABS.some((t) => t.k === q)) return q
    let saved: TabKey | null = null
    try { saved = localStorage.getItem('dsTab') as TabKey | null } catch { /* ignore */ }
    return (saved && TABS.some((t) => t.k === saved) ? saved : 'chat') as TabKey
  })
  const goTab = React.useCallback((k: TabKey) => {
    setTab(k)
    try { localStorage.setItem('dsTab', k) } catch { /* ignore */ }
  }, [])
  const [S, setS] = React.useState<State | null>(null)
  const [err, setErr] = React.useState('')
  const [dark, setDark] = React.useState(() => (localStorage.getItem('dsTheme') || 'light') === 'dark')
  const [drawer, setDrawer] = React.useState(false)
  const [toastMsg, setToastMsg] = React.useState('')
  const [refreshed, setRefreshed] = React.useState(false)
  // 2026-09-18 修"发出去的话过会才出现": toast 原来是普通函数 → 每次父组件重渲染都换身份 →
  //   子组件里依赖它的 useCallback/useEffect 全部重跑 → 拉服务器历史把本地刚发的那条覆盖掉。
  //   用 useCallback 固定身份, 子组件的副作用就只在真正需要时跑。
  const toast = React.useCallback((t: string) => { setToastMsg(t); setTimeout(() => setToastMsg(''), 1800) }, [])

  React.useEffect(() => {
    // DSH 主题靠 body[data-ds-dark-theme] 切换(见 ui-theme/src/styles/design-platform.css)
    if (dark) document.body.setAttribute('data-ds-dark-theme', '')
    else document.body.removeAttribute('data-ds-dark-theme')
    localStorage.setItem('dsTheme', dark ? 'dark' : 'light')
    try {
      TG?.setHeaderColor?.(dark ? '#151517' : '#ffffff')
      TG?.setBackgroundColor?.(dark ? '#151517' : '#ffffff')
    } catch { /* ignore */ }
  }, [dark])

  const reload = React.useCallback(async (quiet = false) => {
    try {
      const s = await api.state()
      setS(s); setErr('')
      if (!quiet) { setRefreshed(true); setTimeout(() => setRefreshed(false), 900) }   // 刷新成功 → 图标 morph 成 ✓
    } catch (e: any) { if (!quiet) setErr(e.message) }
  }, [])

  React.useEffect(() => {
    if (TG) { TG.ready?.(); TG.expand?.() }
    reload()
    const t = setInterval(() => reload(true), 5000)
    return () => clearInterval(t)
  }, [reload])

  const ctx = { S: (S || {}) as State, reload, toast }

  /* ★2026-10-05 老板「dsh是打开一个新页面 你这个是显示在工作流里面的」:
     子代理详情改成**整页打开** —— 状态留在 App 这一层(所以任何页上的子代理行都能唤起),
     由下面渲染一个盖住全屏的 AgentPage。对话页不卸载, 它的 poll 继续跑, 页面里自己再轮询执行记录。 */
  const [agent, setAgent] = React.useState<AgentRef | null>(null)
  const [agentList, setAgentList] = React.useState(false)
  // 从列表进详情时, 详情页盖在列表上面(列表留着), 所以返回键回列表而不是直接回对话
  const [agentFromList, setAgentFromList] = React.useState(false)
  const agentNav = React.useMemo(() => ({
    open: (r: AgentRef) => { setAgentFromList(agentList); setAgent(r) },
    openList: () => { setAgent(null); setAgentFromList(false); setAgentList(true) },
    inList: () => agentList,
  }), [agentList])

  /* 2026-09-21 老板「加一个全屏的 不然电脑的小小一个」。
     正路是 Telegram Bot API 8.0 的 WebApp.requestFullscreen() —— 桌面端点它才会真的把
     mini app 窗口铺满; 老客户端没这两个方法 → 退回浏览器原生 Fullscreen API(在
     Telegram Desktop 的 WebView 里同样有效)。两条路都要监听状态, 否则按钮图标会一直错。*/
  const [fs, setFs] = React.useState(false)
  React.useEffect(() => {
    const W: any = TG
    const sync = () => { try { setFs(!!(W?.isFullscreen ?? document.fullscreenElement)) } catch { /* ignore */ } }
    try {
      W?.onEvent?.('fullscreenChanged', sync)
      W?.onEvent?.('fullscreenFailed', () => { setFs(false); toast('这个客户端不让全屏 —— 也可以直接拉大窗口') })
    } catch { /* ignore */ }
    document.addEventListener('fullscreenchange', sync)
    sync()
    return () => {
      document.removeEventListener('fullscreenchange', sync)
      try { W?.offEvent?.('fullscreenChanged', sync) } catch { /* ignore */ }
    }
  }, [toast])
  const toggleFull = React.useCallback(() => {
    const W: any = TG
    try {
      if (typeof W?.requestFullscreen === 'function') {
        if (W.isFullscreen) W.exitFullscreen?.(); else W.requestFullscreen?.()
        return
      }
    } catch { /* ignore */ }
    try {
      if (document.fullscreenElement) document.exitFullscreen?.()
      else (document.documentElement as any).requestFullscreen?.({ navigationUI: 'hide' })
    } catch { toast('这个客户端不支持全屏') }
  }, [toast])

  /* 2026-09-18 ?debug=1: 把"比视口还宽"的元素列出来(横向溢出排查用, 平时不显示) */
  React.useEffect(() => {
    if (!new URLSearchParams(location.search).get('debug')) return
    const t = setTimeout(() => {
      const vw = document.documentElement.clientWidth
      const bad: string[] = []
      document.querySelectorAll('*').forEach((el) => {
        const r = (el as HTMLElement).getBoundingClientRect()
        if (r.right > vw + 1 || r.width > vw + 1) {
          const cls = String((el as HTMLElement).className || '').slice(0, 46)
          bad.push(`${el.tagName} .${cls} w=${Math.round(r.width)} right=${Math.round(r.right)}`)
        }
      })
      const d = document.createElement('pre')
      d.style.cssText = 'position:fixed;left:0;top:0;z-index:9999;background:#000;color:#0f0;font:10px/1.35 monospace;max-height:100%;overflow:auto;padding:6px;margin:0'
      d.textContent = `vw=${vw} bodyScrollW=${document.body.scrollWidth} docScrollW=${document.documentElement.scrollWidth}\n`
        + (bad.length ? bad.slice(0, 30).join('\n') : '(没有超宽元素)')
      document.body.appendChild(d)
    }, 1800)
    return () => clearTimeout(t)
  }, [S])

  return (
    <AgentNav.Provider value={agentNav}>
    <div className="flex h-full overflow-hidden pad-safe-x">
      {/* 侧栏 */}
      <aside className={cx('z-30 flex w-[232px] shrink-0 flex-col gap-0.5 overflow-y-auto border-r border-line bg-sidebar p-2',
        'fixed inset-y-0 left-0 transition-transform md:static md:translate-x-0', drawer ? 'translate-x-0' : '-translate-x-full')}>
        <div className="flex items-center gap-2 px-2.5 pt-2 pb-3 text-[15px] font-semibold">
          <img src={BOT_AVATAR} alt="" className="h-6 w-6 rounded-[7px] border border-lineweak object-cover" />
          SPECTRE
          <span className={cx('ml-0.5 h-[7px] w-[7px] rounded-full', err ? 'bg-err' : 'bg-ok')} />
        </div>
        <div className="px-2.5 pb-1 text-[11px] uppercase tracking-wide text-fg4">控制台</div>
        {TABS.map(({ k, n, I }) => {
          const cnt = k === 'tasks' ? (S?.tasks.length || 0) : k === 'topics' ? (S?.topics.length || 0)
            : k === 'watch' ? (S?.watch.length || 0) : k === 'files' ? (S?.files.length || 0) : 0
          return (
            <button key={k} onClick={() => { goTab(k); setDrawer(false) }}
              className={cx('flex items-center gap-2.5 rounded-[8px] px-2.5 py-2 text-left text-[13.5px] tap',
                tab === k ? 'bg-soft2 font-semibold text-fg' : 'text-fg2 hover:bg-muted')}>
              <I size={16} className="shrink-0 opacity-80" />
              <span className="flex-1 truncate">{n}</span>
              {cnt > 0 && <span className="rounded-full bg-lineweak px-1.5 text-[11px] text-fg4">{cnt}</span>}
            </button>
          )
        })}
        <div className="mt-auto px-2.5 py-2 text-[11px] text-fg4">
          {S?.name ? `${S.name} · ${S.uid}` : ''}
          <br />仅管理员可用
          <br />前端 {BUILD}
        </div>
      </aside>
      {drawer && <div className="fixed inset-0 z-20 bg-black/50 md:hidden" onClick={() => setDrawer(false)} />}

      {/* 主区 */}
      <section className="flex min-w-0 flex-1 flex-col">
        <header className="pad-safe-t flex h-[50px] shrink-0 items-center gap-1.5 border-b border-line px-2.5 sm:gap-2 sm:px-3.5">
          <Btn variant="ghost" className="md:hidden !px-2" onClick={() => setDrawer(true)}><M icon={I.menu} /></Btn>
          <span className="text-[15px] font-semibold">{TABS.find((t) => t.k === tab)?.n}</span>
          <span className="flex-1" />
          <Btn variant="ghost" className="dsh-iconbtn" onClick={toggleFull} title={fs ? '退出全屏' : '全屏（电脑端窗口小就点这个）'}>
            {fs ? <Minimize2 size={15} /> : <Maximize2 size={15} />}
          </Btn>
          <Btn variant="ghost" className="dsh-iconbtn" onClick={() => setDark(!dark)} title="亮/暗切换">
            <M icon={dark ? I.sun : I.moon} spring="bouncy" />
          </Btn>
          <Btn variant="ghost" className="dsh-iconbtn" onClick={() => reload()} title="刷新">
            <M icon={refreshed ? I.check : I.refresh} />
          </Btn>
          {/* 编译时刻: 对账「改没生效」还是「WebView 吃了旧包」 */}
          <span className="ml-0.5 shrink-0 font-mono text-[10px] tabular-nums text-fg4" title="前端编译时刻">{BUILD}</span>
        </header>

        {/* 2026-09-18 修"往上滑突然跳到底": 聊天页**只留消息区一层滚动**, 外层不再 overflow-y-auto
            (以前内外两层都能滚 → 内容一变外层也自己滚, 看着就是"被拽到底") */}
        <div className={cx('min-h-0 flex-1 p-2 sm:p-3.5', tab === 'chat' ? 'flex flex-col overflow-hidden' : 'overflow-y-auto')}>
          {!INIT ? (
            <Card title="这个控制台要身份才能进">
              <div className="text-[13px] text-fg2">
                ① 私聊机器人发 <code>/web new 手机</code> —— 它会回一条带身份的地址；
                手机浏览器打开那条地址 → 菜单「添加到主屏幕」→ 以后点图标直接进（不用再走 Telegram）。
              </div>
              <div className="mt-1.5 text-[13px] text-fg2">② 手里已经有那条链接的话，整条粘下面回车也行：</div>
              <div className="mt-2">
                <input
                  className="w-full min-w-0 rounded-lg border border-fg4/30 px-2.5 py-1.5 text-[12px] text-fg1 outline-none"
                  placeholder="粘贴 /web 给的整条地址（或那串令牌）"
                  onKeyDown={(e) => {
                    if (e.key !== 'Enter') return
                    const v = (e.target as HTMLInputElement).value.trim()
                    const m = v.match(/[?&#]init=([^&\s]+)/)
                    const t = m ? decodeURIComponent(m[1]) : v
                    if (t) setAuth(t)
                  }}
                />
              </div>
              <div className="mt-1.5 text-[11px] text-fg4">回车确认 —— 整条地址和裸令牌都认。</div>
            </Card>
          ) : err && !S ? (
            <Card title="连不上后端">
              <div className="break-any text-[13px] text-fg2">{err}</div>
              <div className="mt-2"><Btn variant="primary" onClick={() => reload()}>重试</Btn></div>
            </Card>
          ) : !S ? (
            <div className="flex items-center justify-center gap-2 py-10 text-[13px] text-fg3"><Spinner />加载中…</div>
          ) : tab === 'overview' ? <Overview {...ctx} />
            : tab === 'tasks' ? <Tasks {...ctx} />
              : tab === 'chat' ? <Chat {...ctx} />
                : tab === 'topics' ? <Topics {...ctx} />
                  : tab === 'watch' ? <Watch {...ctx} />
                    : tab === 'settings' ? <Settings {...ctx} />
                      : tab === 'files' ? <Files {...ctx} />
                        : <Billing {...ctx} />}
        </div>
      </section>

      <div className={cx('pointer-events-none fixed bottom-6 left-1/2 z-50 -translate-x-1/2 rounded-full border border-line bg-card px-3.5 py-2 text-[13px] shadow-lg transition-opacity',
        toastMsg ? 'opacity-100' : 'opacity-0')}>{toastMsg}</div>

      {/* 子代理记录列表(先渲染) + 详情页(盖在上面; 从列表进来时返回键回列表) */}
      {agentList && <SubListPage onBack={() => { setAgentList(false); setAgentFromList(false) }}
                                 onOpen={(r) => { setAgentFromList(true); setAgent(r) }} />}      {agent && <AgentPage item={agent} fromList={agentFromList}
                           onBack={() => { setAgent(null); setAgentFromList(false) }} />}
    </div>
    </AgentNav.Provider>
  )
}
