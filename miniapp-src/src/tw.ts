/** 网页打字机速度 —— 纯前端偏好(存 localStorage), 跟 TG 那边 bot 自己的 typewriter 开关无关。
 *
 *  ★2026-10-05 老板「这个网页的打字机太慢了吧 我去」:
 *    原来是**写死 10 字/秒**, 只有积压超过 400 字才勉强提到 20 → 一段 500 字的回答要吐 50 秒,
 *    长播报基本等于卡住。
 *    (historically: 2026-09-20 先要"一个字一个字的打", 2026-09-22 又嫌"弹的太快"改成 10/秒 ——
 *    固定值怎么调都会有一头不对, 所以这次不写常数了。)
 *
 *  新规则: 速度由**积压量**算出来 —— cps = 积压字数 / 目标秒数, 再夹在 [floor, ceil] 之间。
 *  花的时间永远约等于 target 秒, 所以"短句有逐字感、长文不会卡住"。档位只是换那个目标秒数。
 */
import * as React from 'react'

export type TwSpeed = 'off' | 'fast' | 'mid' | 'slow'

export const TW: Record<TwSpeed, { n: string; d: string; target: number; floor: number; ceil: number }> = {
  off: { n: '关（直接出）', d: '不要逐字效果，拿到就整段显示', target: 0, floor: 0, ceil: 0 },
  fast: { n: '快', d: '约 1.5 秒吐完（默认）', target: 1.5, floor: 60, ceil: 1500 },
  mid: { n: '中', d: '约 3 秒吐完', target: 3, floor: 30, ceil: 900 },
  slow: { n: '慢', d: '最慢：约 6 秒吐完，短句接近原来一个字一个字的手感', target: 6, floor: 10, ceil: 600 },
}
export const TW_ORDER: TwSpeed[] = ['off', 'fast', 'mid', 'slow']

const KEY = 'dsTwSpeed'
let _cur: TwSpeed = (() => {
  try {
    const v = localStorage.getItem(KEY) as TwSpeed | null
    return v && TW[v] ? v : 'fast'
  } catch { return 'fast' }
})()
const _subs = new Set<() => void>()

export function getTw(): TwSpeed { return _cur }

export function setTw(v: TwSpeed) {
  if (!TW[v] || v === _cur) return
  _cur = v
  try { localStorage.setItem(KEY, v) } catch { /* 隐私模式忽略 */ }
  _subs.forEach((f) => f())
}

/** 改设置页就立刻生效(订阅式, 不用刷新页面) */
export function useTwSpeed(): TwSpeed {
  return React.useSyncExternalStore(
    (cb: () => void) => { _subs.add(cb); return () => { _subs.delete(cb) } },
    () => _cur, () => _cur)
}
