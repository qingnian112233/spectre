/**
 * motion.ts — RikkaHub 同款动效内核
 *
 * 参数不是拍脑袋: 直接取自 androidx.compose.material3 的真实常量
 * (tokens/ExpressiveMotionTokens.kt, VERSION v0_14_0) —— RikkaHub 的 Theme.kt 里
 * 写的是 `motionScheme = MotionScheme.expressive()`, 即整套 M3 Expressive 弹簧:
 *
 *   SpringDefaultSpatialDamping  = 0.8f  SpringDefaultSpatialStiffness  = 380.0f
 *   SpringFastSpatialDamping     = 0.6f  SpringFastSpatialStiffness     = 800.0f
 *   SpringSlowSpatialDamping     = 0.8f  SpringSlowSpatialStiffness     = 200.0f
 *   SpringDefaultEffectsDamping  = 1.0f  SpringDefaultEffectsStiffness  = 1600.0f
 *   SpringFastEffectsDamping     = 1.0f  SpringFastEffectsStiffness     = 3800.0f
 *   SpringSlowEffectsDamping     = 1.0f  SpringSlowEffectsStiffness     = 800.0f
 *
 * 为什么"舒服": CSS 的 cubic-bezier 是**固定时长**的曲线; 弹簧是**物理**——
 * 阻尼 0.8 / 刚度 380 时它会在 ~443ms 里带 1.5% 的过冲收住, 刚度 800 时 ~407ms 带 9.5% 过冲。
 * 中断时的速度会被下一段动画接着用, 所以连点两次不会有"卡一下再重来"的顿挫。
 *
 * 本文件提供三样东西:
 *   ① M3 常量 + 解析解 -> CSS `linear()` 缓动字符串 (给纯 CSS 动画用, 轨迹和弹簧一模一样)
 *   ② SpringRunner: rAF 弹簧积分器 (给高度/尺寸这种必须逐帧算的值用, 等价 animateContentSize)
 *   ③ <SpringFold>: 折叠面板, 用真实高度弹簧展开(不是 max-height 障眼法)
 */
import * as React from 'react'

export type SpringSpec = { zeta: number; k: number }

/** M3 Expressive MotionScheme 全套弹簧参数(与 androidx 常量逐位一致) */
export const M3: Record<string, SpringSpec> = {
  defaultSpatial: { zeta: 0.8, k: 380 },
  fastSpatial: { zeta: 0.6, k: 800 },
  slowSpatial: { zeta: 0.8, k: 200 },
  defaultEffects: { zeta: 1.0, k: 1600 },
  fastEffects: { zeta: 1.0, k: 3800 },
  slowEffects: { zeta: 1.0, k: 800 },
}

/** 阻尼谐振子解析解: x(0)=0, x'(0)=0, x(∞)=1 —— 和 Compose SpringSimulation 同一条公式 */
export function springProgress(s: SpringSpec, t: number): number {
  const w0 = Math.sqrt(s.k)
  const z = s.zeta
  if (z < 1) {
    const wd = w0 * Math.sqrt(1 - z * z)
    return 1 - Math.exp(-z * w0 * t) * (Math.cos(wd * t) + ((z * w0) / wd) * Math.sin(wd * t))
  }
  if (z === 1) return 1 - Math.exp(-w0 * t) * (1 + w0 * t)
  const wr = w0 * Math.sqrt(z * z - 1)
  const a = -z * w0 + wr
  const b = -z * w0 - wr
  return 1 - (b * Math.exp(a * t) - a * Math.exp(b * t)) / (b - a)
}

/** 落地时长: 包络衰减到 precision(默认千分之一) 所需时间, 毫秒 */
export function springDuration(s: SpringSpec, precision = 0.001): number {
  const decay = s.zeta >= 1 ? Math.sqrt(s.k) : s.zeta * Math.sqrt(s.k)
  return Math.round((1000 * -Math.log(precision)) / decay)
}

const easingCache = new Map<string, string>()

/** 弹簧 -> CSS `linear()` 缓动串 (Chrome 113+ / Safari 17.2+ 支持; 不支持则回落到 cubic-bezier) */
export function springEasing(s: SpringSpec, samples = 80): string {
  const key = `${s.zeta}/${s.k}/${samples}`
  const hit = easingCache.get(key)
  if (hit) return hit
  const dur = springDuration(s) / 1000
  const pts: string[] = []
  for (let i = 0; i <= samples; i++) {
    const v = i === 0 ? 0 : i === samples ? 1 : springProgress(s, (i / samples) * dur)
    pts.push(v.toFixed(4))
  }
  const out = `linear(${pts.join(',')})`
  easingCache.set(key, out)
  return out
}

/** 一次性拿到 {duration, easing, value} 三件套, 方便塞进 style */
export function springStyle(s: SpringSpec) {
  return { duration: springDuration(s), easing: springEasing(s) }
}

/** 给 CSS 用的变量表(挂到 :root, 见 index.css) */
export const SPRING_CSS_VARS: Record<string, string> = {
  '--spring-default-spatial': springEasing(M3.defaultSpatial),
  '--spring-fast-spatial': springEasing(M3.fastSpatial),
  '--spring-slow-spatial': springEasing(M3.slowSpatial),
  '--spring-default-effects': springEasing(M3.defaultEffects),
  '--spring-fast-effects': springEasing(M3.fastEffects),
  '--spring-slow-effects': springEasing(M3.slowEffects),
}

/**
 * 弹簧积分器: 半隐式欧拉, 固定 1/240s 子步(mass = 1, 和 Compose 同模型)
 *   a = -k·x - c·v ,  c = 2ζ√k
 * 关键: 速度**跨段保留** —— 上一次还没跑完就改目标, 会从当前速度续着走(Compose interrupted spring 行为)
 */
/**
 * 全局「动效帧」钩子。
 *
 * 为什么需要: 弹簧是 JS 逐帧写高度的 —— 但 DOM 侧没人知道"内容还在长",
 * 贴底的消息列表不会跟着重钉底部, 于是新内容会长到可视区外面, 等下一条数据(最快 1s)才啪地拉回。
 *
 * 用法: 容器注册一个回调, 任何一个 SpringRunner 的每一帧都会调它一下。
 *   Chat 里就是这么用的 —— 只要还处于"贴底"状态, 每一帧都把 scrollTop 钉到 scrollHeight,
 *   于是"底边固定、内容从底边长出来", 中间不会露馅。
 */
let motionFrameHook: (() => void) | null = null

export function setMotionFrameHook(cb: (() => void) | null): void {
  motionFrameHook = cb
}

export class SpringRunner {
  private raf = 0
  private x = 0
  private v = 0
  private last = 0
  private target = 0
  private spec: SpringSpec
  private stopped = false

  constructor(private onFrame: (value: number) => void, spec: SpringSpec = M3.defaultSpatial) {
    this.spec = spec
  }

  /** 设新目标; 传 spec 可换刚度(同一段里换挡也是合法的) */
  set(target: number, spec?: SpringSpec): void {
    this.target = target
    if (spec) this.spec = spec
    if (this.stopped) return
    if (!this.raf) {
      this.last = 0
      this.raf = requestAnimationFrame(this.tick)
    }
  }

  /** 立刻跳到目标(首帧不要让动画从 0 爬起来) */
  jump(value: number): void {
    this.target = value
    this.x = value
    this.v = 0
    this.onFrame(value)
  }

  stop(): void {
    this.stopped = true
    if (this.raf) cancelAnimationFrame(this.raf)
    this.raf = 0
  }

  private tick = (now: number): void => {
    if (!this.last) this.last = now - 16
    let dt = Math.min((now - this.last) / 1000, 0.064) // 掉帧时别让弹簧炸掉
    this.last = now

    const { zeta, k } = this.spec
    const c = 2 * zeta * Math.sqrt(k)
    const step = 1 / 240
    while (dt > 0) {
      const h = Math.min(step, dt)
      const x = this.x - this.target
      this.v += h * (-k * x - c * this.v)
      this.x += h * this.v
      dt -= h
    }

    const dx = this.x - this.target
    if (Math.abs(dx) < 0.05 && Math.abs(this.v) < 0.5) {
      this.x = this.target
      this.v = 0
      this.onFrame(this.x)
      this.raf = 0
      this.last = 0
      return
    }
    this.onFrame(this.x)
    if (motionFrameHook) { try { motionFrameHook() } catch { /* 钩子出错别拖垮动画 */ } }
    this.raf = requestAnimationFrame(this.tick)
  }
}

/**
 * <SpringIn> — 新行进场(默认「纯高度弹簧 + 淡入」)。
 *
 * 2026-09-30 修正: 上一版照 Compose 的 `slideInVertically { it / 2 }` 加了 translateY(自身高度/2),
 *   但那条位移在**贴底列表**里是反效果 —— 行从下往上滑, 起点在可视区之外(输入条下面),
 *   观感就是"新内容从最底下弹出来"。RikkaHub 的真实做法是:
 *     · 消息本体/步骤条目 **不做位移**, 靠 `Modifier.animateContentSize()` 让容器高度按弹簧长高;
 *     · 只有"操作按钮行"和"推理标题切换"才用 slideInVertically。
 *   所以这里默认 slide=0(纯高度), 需要位移的地方显式传 slide=像素数。
 *
 * 高度从 0 弹到自然高度, 内容随高度裁切 —— 贴底容器里就是"从底边长出来"。
 */
export function SpringIn({
  children,
  className,
  spec = M3.defaultSpatial,
  disabled,
  slide = 0,
}: {
  children: React.ReactNode
  className?: string
  spec?: SpringSpec
  disabled?: boolean
  /** 进场位移(px)。0=不做位移(默认, 同 animateContentSize); 正数=从下方滑入, 负数=从上方滑入 */
  slide?: number
}) {
  const outer = React.useRef<HTMLDivElement | null>(null)
  const runner = React.useRef<SpringRunner | null>(null)
  const natural = React.useRef(0)
  const slideRef = React.useRef(slide)
  slideRef.current = slide

  if (!runner.current) {
    runner.current = new SpringRunner((h) => {
      const o = outer.current
      if (!o) return
      const clamped = Math.max(0, h)
      const n = Math.max(natural.current, 1)
      o.style.height = clamped >= n - 0.5 ? 'auto' : `${clamped}px`
      const p = Math.min(1, Math.max(0, clamped / n))
      o.style.setProperty('--in-p', String(p))
      o.style.opacity = String(p)
      const sl = slideRef.current
      o.style.transform = sl ? `translateY(${(1 - p) * sl}px)` : 'none'
    }, spec)
  }

  React.useEffect(() => {
    const el = outer.current
    if (!el) return
    if (disabled) { el.style.height = 'auto'; el.style.opacity = '1'; el.style.transform = 'none'; return }
    const h = el.scrollHeight
    natural.current = h
    el.style.height = '0px'
    el.style.opacity = '0'
    if (slideRef.current) el.style.transform = `translateY(${slideRef.current}px)`
    runner.current!.jump(0)
    runner.current!.set(h, spec)
    return () => runner.current?.stop()
  }, [])

  return (
    <div ref={outer} className={['springin', className].filter(Boolean).join(' ')} style={{ overflow: 'hidden' }}>
      <div>{children}</div>
    </div>
  )
}

/**
 * <SpringGrow> — 等价 Compose 的 `Modifier.animateContentSize(spec)`。
 *
 * 区别在于**什么时候动**:
 *   · <SpringIn>    = 元素第一次出现(0 → 自然高度), 用于新行;
 *   · <SpringGrow>  = 元素一直在, 但内容变长了(旧高度 → 新高度), 用于流式追加的文本块。
 * 首帧不播(直接跳到位, 同 animateContentSize 的初始测量行为), 之后每次内容高度变化都走弹簧。
 * 用 ResizeObserver 量内层自然高度 —— 不用轮询, 内容换行/图片加载/代码块展开都能跟。
 */
export function SpringGrow({
  children,
  className,
  spec = M3.defaultSpatial,
  initial = false,
}: {
  children: React.ReactNode
  className?: string
  spec?: SpringSpec
  /** 首次出现也走弹簧(0 → 自然高度)。默认 false = 首帧直接到位(纯 animateContentSize 语义) */
  initial?: boolean
}) {
  const outer = React.useRef<HTMLDivElement | null>(null)
  const inner = React.useRef<HTMLDivElement | null>(null)
  const runner = React.useRef<SpringRunner | null>(null)
  const seen = React.useRef(false)
  const target = React.useRef(0)

  if (!runner.current) {
    runner.current = new SpringRunner((h) => {
      const o = outer.current
      if (!o) return
      const clamped = Math.max(0, h)
      const n = Math.max(target.current, 1)
      const p = Math.min(1, Math.max(0, clamped / n))
      o.style.height = Math.abs(clamped - target.current) < 0.5 ? 'auto' : `${clamped}px`
      o.style.opacity = String(p)
    }, spec)
  }

  React.useEffect(() => {
    const el = inner.current
    if (!el) return
    const apply = () => {
      const h = el.getBoundingClientRect().height
      target.current = h
      if (!seen.current) {
        seen.current = true
        if (!initial) { runner.current!.jump(h); return }
        const o = outer.current
        if (o) { o.style.height = '0px'; o.style.opacity = '0' }
        runner.current!.jump(0)
      }
      runner.current!.set(h, spec)
    }
    apply()
    if (typeof ResizeObserver === 'undefined') return () => runner.current?.stop()
    const ro = new ResizeObserver(apply)
    ro.observe(el)
    return () => { ro.disconnect(); runner.current?.stop() }
  }, [])

  return (
    <div ref={outer} className={['springgrow', className].filter(Boolean).join(' ')} style={{ overflow: 'hidden' }}>
      <div ref={inner}>{children}</div>
    </div>
  )
}

export function useSpring(initial: number, spec: SpringSpec = M3.defaultSpatial) {
  const [, force] = React.useState(0)
  const valueRef = React.useRef(initial)
  const runnerRef = React.useRef<SpringRunner | null>(null)

  if (!runnerRef.current) {
    runnerRef.current = new SpringRunner((v) => {
      valueRef.current = v
      force((n) => n + 1)
    }, spec)
  }
  React.useEffect(() => () => runnerRef.current?.stop(), [])

  const set = React.useCallback((target: number, s?: SpringSpec) => runnerRef.current?.set(target, s), [])
  const jump = React.useCallback((v: number) => runnerRef.current?.jump(v), [])
  return [valueRef.current, set, jump] as const
}

/**
 * <SpringFold> — 折叠面板。
 * 承担 Compose 里 `Modifier.animateContentSize(motionScheme.defaultSpatialSpec())` 的角色:
 * 逐帧给外层写真实高度(px), 内容用 ResizeObserver 量自然高度, 打开/收起/内容变长都能跟。
 * 顺带把内容透明度按展开比例给到, 于是"长出来"和"淡进来"是同一条弹簧。
 */
export function SpringFold({
  open,
  children,
  className,
  innerClassName,
  spec = M3.defaultSpatial,
  overshoot = true,
}: {
  open: boolean
  children: React.ReactNode
  className?: string
  innerClassName?: string
  spec?: SpringSpec
  /** 允许过冲(高度超过内容一点再回落 = 弹性感); 关掉则是纯减速 */
  overshoot?: boolean
}) {
  const outer = React.useRef<HTMLDivElement | null>(null)
  const inner = React.useRef<HTMLDivElement | null>(null)
  const runner = React.useRef<SpringRunner | null>(null)
  const natural = React.useRef(0)
  const openedRef = React.useRef(open)
  const useSpec = overshoot ? spec : { zeta: 1, k: spec.k }

  if (!runner.current) {
    runner.current = new SpringRunner((h) => {
      const o = outer.current
      if (!o) return
      const n = Math.max(natural.current, 1)
      const clamped = Math.max(0, h)
      o.style.height = clamped < 0.5 ? '0px' : `${clamped}px`
      // 内容透明度跟着展开比例走(0 → 1)
      const p = Math.min(1, Math.max(0, clamped / n))
      o.style.setProperty('--fold-p', String(p))
    }, useSpec)
  }

  // 量内容自然高度; 内容变化(流式输出/换了内容)也要跟着弹
  React.useEffect(() => {
    const el = inner.current
    if (!el) return
    const measure = () => {
      const h = el.offsetHeight
      if (Math.abs(h - natural.current) < 0.5) return
      const isFirst = natural.current === 0
      natural.current = h
      if (!openedRef.current) return
      if (isFirst) runner.current?.jump(h)
      else runner.current?.set(h)
    }
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // open 变化 -> 弹过去
  React.useEffect(() => {
    const r = runner.current!
    if (open) {
      openedRef.current = true
      const n = natural.current
      if (n > 0) r.set(n, useSpec)
      else r.jump(0)
    } else {
      openedRef.current = false
      r.set(0, useSpec)
    }
  }, [open])

  React.useEffect(() => () => runner.current?.stop(), [])

  return (
    <div
      ref={outer}
      className={['springfold', className].filter(Boolean).join(' ')}
      style={{ height: 0, overflow: 'hidden', willChange: 'height' }}
    >
      <div ref={inner} className={innerClassName}>
        {children}
      </div>
    </div>
  )
}
