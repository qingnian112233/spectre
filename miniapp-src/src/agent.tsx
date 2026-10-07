/** 子代理详情页 —— 整页打开(和 DSH 一致), 不是在工作流里就地展开。
 *
 *  ★2026-10-05 老板「dsh是打开一个新页面 你这个是显示在工作流里面的 ...」:
 *    原来只在成员行下面塞一段可折叠正文; 现在改成 App 层渲染的**全屏页**:
 *    盖住整个小程序(含顶栏和底部标签栏), 自带「← 返回」头部, 所以视觉上就是"进去了、能出来"。
 *
 *  为什么盖一层而不是卸载对话页: 对话页里跑着 /api/chat/poll 的轮询 —— 卸了它, 子代理跑到哪就看不到了。
 *  这里自己轮询 /api/sub/log(只查本地 8901, 开销极小), 直到跑完为止, 所以"点开就不动"也能实时跟进。
 */
import * as React from 'react'
import { api, fmt, type SubLog, type SubItem } from './api'
import { Markdown } from './md'
import { Btn, Spinner, Tag, cx, useTapClose } from './ui'
import type { AgentRef } from './nav'

type Tab = 'log' | 'board' | 'out'

/** role → [可读名, 文字色, 左边色条]。类名写全(Tailwind 扫不到拼接出来的类名)。
 *
 *  ★2026-10-07 老板「分层做好一点」「思考没有追加吗」「框框太淡了 颜色搞好点」:
 *    ① 原来每条记录都是一条同样粗细的 border-lineweak 竖线 —— 分不出谁在说话, 而且暗色下
 *       几乎看不见(就是"太淡")。现在按角色给: 左侧 3px 角色色条 + 缩进层级 + 玻璃卡。
 *    ② 补上 reasoning/think 两档 —— 子代理的思考以前落进"其它", 灰成一团认不出来。 */
const ROLE_TXT: Record<string, [string, string, string]> = {
  system: ['系统段 · 它的身份与规矩', 'text-fg3', 'border-l-role-other'],
  user: ['派给它的活', 'text-accent', 'border-l-accent'],
  reasoning: ['思考', 'text-fg3', 'border-l-role-audit'],
  think: ['思考', 'text-fg3', 'border-l-role-audit'],
  assistant: ['它这一轮的输出', 'text-role-recon', 'border-l-role-recon'],
  tool: ['工具返回', 'text-role-lateral', 'border-l-role-lateral'],
}
const roleTxt = (r: string): [string, string, string] =>
  ROLE_TXT[r] || [(r || '其它'), 'text-fg3', 'border-l-role-other']
/** 次级层(思考/工具返回): 缩进一层 + 换更薄的玻璃 —— 主线(派活 / 它的输出)才占满宽 */
const SUB_ROLE = new Set(['reasoning', 'think', 'tool'])

export function AgentPage({ item, onBack, fromList }: { item: AgentRef; onBack: () => void; fromList?: boolean }) {
  const [d, setD] = React.useState<SubLog | null>(null)
  const [err, setErr] = React.useState('')
  const [tab, setTab] = React.useState<Tab>('log')
  const [sysOpen, setSysOpen] = React.useState(false)      // system 段长, 默认收起来
  // ★2026-10-05 老板「展开了 点击他的里面的其他地方 怎么不收起来」→ 点里面也收(拖动选字不算)
  const { ref: sysBox, props: sysTap } = useTapClose(sysOpen, () => setSysOpen(false))
  const [follow, setFollow] = React.useState(true)         // 贴着底部跟着跑(DSH 那种自动滚)
  const boxRef = React.useRef<HTMLDivElement | null>(null)
  const busyRef = React.useRef(false)
  const [tick, setTick] = React.useState(0)

  // 拉一次(手动重试也走它)
  const pull = React.useCallback(async () => {
    if (busyRef.current) return
    busyRef.current = true
    try {
      const r = await api.subLog(item.k, item.idx)
      // 内容没变就**还回原来的对象** —— React 看到同引用会跳过这次重渲染,
      // 否则每 2 秒一次新对象会把整条消息链重画一遍(还会把滚动位置顶回去)。
      setD((prev) => (prev && prev.st === r.st && prev.rnd === r.rnd && prev.tools === r.tools
        && prev.el === r.el && prev.log.length === r.log.length && prev.board.length === r.board.length
        && (prev.out || '').length === (r.out || '').length ? prev : r))
      setErr('')
    } catch (e: any) {
      setErr(String((e && e.message) || e))
    } finally {
      busyRef.current = false
    }
  }, [item.k, item.idx])

  React.useEffect(() => { pull() }, [pull])

  // 跑着就一直跟着(2s); 跑完停 —— 不白刷。依赖只取状态字, 免得每拉一次就重建定时器。
  const runSt = d?.st || 'run'
  React.useEffect(() => {
    if (d && d.st !== 'run') return
    const t = setInterval(() => { pull(); setTick((x) => x + 1) }, 2000)
    return () => clearInterval(t)
  }, [runSt, d, pull])

  // 新的记录来了 + 跟着底部 → 滚到底(用户手动往上翻了就停跟随)
  const nLog = d?.log.length || 0
  React.useEffect(() => {
    const el = boxRef.current
    if (!el || !follow) return
    el.scrollTop = el.scrollHeight
  }, [nLog, tick, follow, tab])

  const onScroll = () => {
    const el = boxRef.current
    if (!el) return
    setFollow(el.scrollHeight - el.scrollTop - el.clientHeight < 40)
  }

  // Esc 返回(和 DSH 一样的手感)
  React.useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onBack() }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [onBack])

  const st = err && !d ? 'err' : (d?.st || 'run')
  const tabs: [Tab, string, number][] = d
    ? [['log', '执行记录', d.log.length], ['board', '队友黑板', d.board.length], ['out', '最终产出', (d.out || '').length]]
    : []

  return (
    <div className="fixed inset-0 z-[60] flex flex-col bg-bg">
      {/* 头部: 返回 + 标题(和主壳同一套尺寸, 所以看着就是"进了另一页") */}
      <header className="pad-safe-t flex h-[50px] shrink-0 items-center gap-1.5 border-b border-line px-2.5 sm:gap-2.5 sm:px-3.5">
        <Btn variant="ghost" onClick={onBack} title={fromList ? '返回列表(Esc)' : '返回对话(Esc)'}>
          <span className="mr-1 font-mono">←</span>{fromList ? '列表' : '返回'}
        </Btn>
        <span className="minw0 flex-1 truncate text-[14px] font-semibold">{item.task || '子代理'}</span>
        <Tag tone={st === 'done' ? 'ok' : st === 'fail' || st === 'err' ? 'err' : 'warn'}>
          {st === 'done' ? '已完成' : st === 'fail' ? '已中断' : st === 'err' ? '取不到' : '运行中'}
        </Tag>
      </header>

      {/* 关键信息一行(角色 / 哪个工具起的 / 进度) */}
      <div className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-0.5 border-b border-lineweak px-3 py-1.5 text-[11.5px] text-fg3">
        <span>起它的工具: <span className="font-mono text-fg2">{item.host || '子代理'}</span></span>
        <span>角色: <span className="font-mono text-fg2">{item.role || '其它'}</span></span>
        {d && <>
          {d.rnd ? <span>第 <span className="font-mono text-fg2">{d.rnd}</span> 轮</span> : null}
          {d.tools ? <span><span className="font-mono text-fg2">{d.tools}</span> 个工具</span> : null}
          {d.el ? <span>耗时 <span className="font-mono text-fg2">{fmt.sec(d.el)}</span></span> : null}
        </>}
        <span className="ml-auto font-mono text-fg4">#{item.idx + 1}</span>
      </div>

      {/* 页签 */}
      <div className="flex shrink-0 items-center gap-3 border-b border-line px-3 py-1.5 text-[12px]">
        {tabs.map(([k, nm, n]) => (
          <span key={k} onClick={() => setTab(k)}
            className={cx('cursor-pointer select-none font-mono', tab === k ? 'text-accent' : 'text-fg3 hover:text-fg2')}>
            {nm} {n}
          </span>
        ))}
        {d && d.st === 'run' && <span className="ml-auto flex items-center gap-1.5 text-[11px] text-accent">
          <Spinner /> 还在跑, 每 2 秒自动跟上</span>}
        {d && d.st !== 'run' && <span className="ml-auto text-[11px] text-fg4">已停更</span>}
      </div>

      {/* 正文 */}
      <div ref={boxRef} onScroll={onScroll} className="min-h-0 flex-1 overflow-y-auto px-3 py-2.5">
        {err && !d ? (
          <div className="text-[13px] text-fg2">
            取不到这条记录: {err}
            <div className="mt-2"><Btn variant="outline" onClick={() => pull()}>重试</Btn></div>
          </div>
        ) : !d ? (
          <div className="flex items-center justify-center gap-2 py-10 text-[13px] text-fg3"><Spinner />取它的执行记录…</div>
        ) : tab === 'log' ? (
          d.log.length ? d.log.map((m, i) => {
            const [nm, cls, bar] = roleTxt(m.r)
            const isSys = m.r === 'system'
            const isThink = m.r === 'reasoning' || m.r === 'think'
            const sub = SUB_ROLE.has(m.r)
            // ★2026-10-05 老板「子代理回复带上富文本」:
            //   它的输出(assistant)以前是当纯文本原样铺出来 —— `**粗体**`、``` 代码块、表格全是原文。
            //   现在 assistant 走 <Markdown>(marked + DOMPurify, 和主对话里那条回复同一套渲染)。
            //   工具返回/系统段**保持等宽纯文本**: 那是机器正文(JSON/命令行输出), 富文本反而更难读。
            const rich = m.r === 'assistant'
            // ★2026-10-05 老板「展开的内容 点击任意的就关起来 不是点击那个才关起来」:
            //   系统段展开之后, 点这段以外任何地方都收回去(以前只能再点那个"展开全文"的小字,
            //   展开态还没有收起入口 —— 等于关不掉)。收起的判定挂在 sysBox 上, 见 useOutsideClose。
            const body = isSys && !sysOpen
              ? <div className="mt-0.5 cursor-pointer select-none whitespace-pre-wrap break-any font-mono text-[11.5px] leading-[1.6] text-fg4"
                  onClick={() => setSysOpen(true)}>
                  {m.t.slice(0, 220)}…
                  <span className="ml-1 text-accent">展开全文(这是子代理自己的系统段)</span>
                </div>
              : rich
                ? <div className="mt-1"><Markdown text={m.t} /></div>
                : <div ref={isSys ? sysBox : undefined} {...(isSys ? sysTap : {})}
                    className="mt-0.5 whitespace-pre-wrap break-any font-mono text-[11.5px] leading-[1.6] text-fg2">
                    {m.t}
                    {isSys ? <div className="mt-1 cursor-pointer select-none font-mono text-[10.5px] text-accent"
                      onClick={() => setSysOpen(false)}>收起(点别处也收)</div> : null}
                  </div>
            return (
              <div key={i} className={cx('lg lg-bar mb-2 rounded-[12px] py-1.5 pr-2.5',
                sub ? 'lg-sub ml-4 pl-2.5' : 'pl-3', bar)}>
                <div className={cx('flex items-center gap-1.5 font-mono text-[10.5px] tracking-wide', cls)}>
                  {isThink ? <span className="opacity-70">◇</span> : null}
                  <span>{nm}</span>
                </div>
                {body}
              </div>
            )
          }) : <div className="py-2 text-[12.5px] text-fg3">还没有留存执行记录{d.out ? ' —— 看「最终产出」页签' : ''}</div>
        ) : tab === 'board' ? (
          d.board.length ? d.board.map((b, i) => (
            <div key={i} className="lg lg-bar mb-2 rounded-[12px] border-l-role-lateral py-1.5 pl-3 pr-2.5">
              <div className="font-mono text-[10.5px] text-role-lateral">{b.from || '队友'}
                <span className="ml-2 text-fg4">{b.ts ? new Date(b.ts * 1000).toLocaleTimeString() : ''}</span></div>
              <div className="mt-1"><Markdown text={b.text} /></div>
            </div>
          )) : <div className="py-2 text-[12.5px] text-fg3">
            这批代理之间没有在黑板说话。多个子代理一起干活时, 它们的 SAY/ASK 会出现在这里。
          </div>
        ) : (
          d.out ? <Markdown text={d.out} />
            : <div className="py-2 text-[12.5px] text-fg3">还没产出(还在跑)</div>
        )}
      </div>
    </div>
  )
}

/** 最近的子代理 —— **跑完之后"怎么重新打开那个"的入口**。
 *
 *  ★2026-10-05 老板「子代理开了 我进去了 怎么重新打开那个？」:
 *    以前成员行只跟着当前 job 的 running 状态渲染(跑完 `setJob(null)` → 整块消失), 刷新更没入口。
 *    这里读服务端持久缓存(/api/sub/list), 进程内保留最近 240 条、列表按最后更新倒序。
 *    自己带 5 秒轮询, 所以开着它还能看到正在跑的条目状态在变。
 */
export function SubListPage({ onBack, onOpen, onCleared }:
  { onBack: () => void; onOpen: (r: AgentRef) => void; onCleared?: (items: SubItem[]) => void }) {
  const [items, setItems] = React.useState<SubItem[] | null>(null)
  const [err, setErr] = React.useState('')

  const pull = React.useCallback(async () => {
    try {
      const r = await api.subList(40)
      setItems(r.items || []); setErr('')
    } catch (e: any) {
      setErr(String((e && e.message) || e))
    }
  }, [])
  React.useEffect(() => { pull() }, [pull])
  React.useEffect(() => {
    const t = setInterval(pull, 5000)
    return () => clearInterval(t)
  }, [pull])
  React.useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onBack() }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [onBack])

  const running = (items || []).filter((x) => x.st === 'run').length
  return (
    <div className="fixed inset-0 z-[55] flex flex-col bg-bg">
      <header className="pad-safe-t flex h-[50px] shrink-0 items-center gap-1.5 border-b border-line px-2.5 sm:gap-2.5 sm:px-3.5">
        <Btn variant="ghost" onClick={onBack} title="返回对话(Esc)">
          <span className="mr-1 font-mono">←</span>返回
        </Btn>
        <span className="minw0 flex-1 truncate text-[14px] font-semibold">子代理记录</span>
        <span className="shrink-0 font-mono text-[11px] text-fg3">
          {running ? `运行中 ${running}` : `${(items || []).length} 条`}
        </span>
        {/* ★2026-10-05 老板「如果我清空话题 下面应该不显示出来了吧」:
            清单现在按自己 uid 过滤(清空对话/刷新都不会再挂在那儿), 这里再给一个手动清空。 */}
        {!!(items || []).length && (
          <Btn variant="ghost" title="清空这些记录（不影响 Telegram / 会话内容）"
            onClick={async () => {
              try { const r = await api.subClear(); setItems(r.items || []); onCleared?.(r.items || []) }
              catch (e: any) { setErr(String((e && e.message) || e)) }
            }}>清空</Btn>
        )}
        <Btn variant="ghost" onClick={() => pull()} title="刷新">刷新</Btn>
      </header>

      <div className="shrink-0 border-b border-lineweak px-3 py-1.5 text-[11.5px] text-fg4">
        点任意一条进入它的完整执行记录。这里保留最近 240 条(进程重启会清空)。
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-2">
        {err && !items ? (
          <div className="text-[13px] text-fg2">取不到列表: {err}
            <div className="mt-2"><Btn variant="outline" onClick={() => pull()}>重试</Btn></div>
          </div>
        ) : !items ? (
          <div className="flex items-center justify-center gap-2 py-10 text-[13px] text-fg3"><Spinner />加载…</div>
        ) : !items.length ? (
          <div className="py-6 text-center text-[13px] text-fg3">
            还没有记录。<br />派一个子任务(让SPECTRE用 subagent 干活)之后这里就会出现。
          </div>
        ) : items.map((it) => {
          const sk = it.key + ':' + it.idx
          return (
            <div key={sk} onClick={() => onOpen({ host: it.tool || '子代理', task: it.task || '子任务',
                                                  role: it.role, k: it.key, idx: it.idx })}
              className="lg mb-1.5 cursor-pointer rounded-[12px] px-2.5 py-2 tap hover:brightness-110">
              <div className="flex items-center gap-2">
                <span className={cx('shrink-0 font-mono',
                  it.st === 'done' ? 'text-ok' : it.st === 'fail' ? 'text-err' : 'text-accent')}>
                  {it.st === 'done' ? '●' : it.st === 'fail' ? '✕' : '⋯'}
                </span>
                <span className="minw0 flex-1 truncate text-[12.5px] text-fg">{it.task || '子任务'}</span>
                <span className="shrink-0 font-mono text-accent">打开 ▸</span>
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 font-mono text-[10.5px] text-fg4">
                <span>{it.tool || '子代理'}</span>
                {it.role ? <span>角色 {it.role}</span> : null}
                {it.rnd ? <span>第{it.rnd}轮</span> : null}
                {it.tools ? <span>{it.tools}工具</span> : null}
                {it.el ? <span>{fmt.sec(it.el)}</span> : null}
                {it.nLog ? <span>{it.nLog}步</span> : null}
                {it.nBoard ? <span className="text-role-lateral">黑板{it.nBoard}</span> : null}
                <span className="ml-auto">{it.ts ? new Date(it.ts * 1000).toLocaleTimeString() : ''}</span>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
