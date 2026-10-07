/** 状态驱动的图标一律用 morphicons(开源 MIT, morphicons.com)做**变形动画**:
 *  亮暗切换、抽屉开合、刷新完成、展开/收起 —— 换状态时图标自己"长"过去, 不是硬切。
 *
 *  2026-09-18 踩坑记录(白屏事故): `lucide@0.469` 的导出是**整棵 svg 树**
 *  `["svg", {attrs}, [[tag, attrs], ...]]`, 而 morphicons 要的是 IconNode
 *  `[[tag, attrs], ...]` —— 直接丢进去会抛 "morphicons: unsupported tag <s>"(它把 "svg" 的 s 当标签),
 *  React 一渲染就整页白。所以这里统一做适配 + 可用性校验, 不合法就退回静态图标, 绝不白屏。
 */
import * as React from 'react'
import { MorphIcon } from 'morphicons/react'
import {
  Menu as IMenu, X as IX, Sun as ISun, Moon as IMoon, RefreshCw as IRefresh, Check as ICheck,
  ChevronRight as IChevRight, ChevronDown as IChevDown, Send as ISend, Square as ISquare,
  Plus as IPlus, Eye as IEye,
} from 'lucide'

export const I = {
  menu: IMenu, x: IX, sun: ISun, moon: IMoon, refresh: IRefresh, check: ICheck,
  chevRight: IChevRight, chevDown: IChevDown, send: ISend, square: ISquare, plus: IPlus, eye: IEye,
}

const OK_TAGS = new Set(['path', 'line', 'circle', 'ellipse', 'rect', 'polyline', 'polygon'])

/** lucide 各版本形态不一: 有 `[[tag,attrs],…]`, 也有 `["svg",{attrs},[[tag,attrs],…]]` */
function toNodes(raw: any): any[] {
  let n = raw
  if (Array.isArray(n) && n[0] === 'svg') n = n[2]
  if (!Array.isArray(n)) return []
  return n.filter((x) => Array.isArray(x) && typeof x[0] === 'string')
}

function morphable(raw: any): boolean {
  const n = toNodes(raw)
  if (!n.length) return false
  return n.every((x) => OK_TAGS.has(String(x[0])))
}

/** 退路: 用纯 SVG 画出来(静态, 至少图标不缺) */
function StaticSvg({ nodes, size = 16, strokeWidth = 2, className }: { nodes: any[]; size?: number; strokeWidth?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">
      {nodes.map((x, i) => React.createElement(String(x[0]), { key: i, ...(x[1] || {}) }))}
    </svg>
  )
}

type Props = {
  icon: unknown
  size?: number
  strokeWidth?: number
  className?: string
  spring?: 'smooth' | 'snappy' | 'bouncy'
  label?: string
}

/** 统一的图标组件: 能 morph 就 morph, 不能就静态渲染 */
export function M({ icon, size = 16, strokeWidth = 2, className, spring = 'snappy', label }: Props) {
  const nodes = React.useMemo(() => toNodes(icon), [icon])
  if (!morphable(icon)) {
    return <StaticSvg nodes={nodes} size={size} strokeWidth={strokeWidth} className={className} />
  }
  return (
    <MorphIcon icon={nodes as any} size={size} strokeWidth={strokeWidth}
      className={className} spring={spring} {...(label ? { label } : {})} />
  )
}
