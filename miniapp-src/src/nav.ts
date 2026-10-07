/** 子代理详情: 打开/关闭的跨层通道。
 *  为什么要单独一个 context: 子代理行在「对话」页的深处(SubPanel → 成员行),
 *  而"打开一个新页面"要由 App 那一层来渲染(和 DSH 一样: 点开就离开当前视图, 不是在工作流里展开)。
 *  夹在中间的几层组件不需要知道这件事 —— 只传一个 open 回调下去。 */
import * as React from 'react'

export type AgentRef = {
  /** 第一层标题: 哪个工具起的(subagent / team / workflow / ralph) */
  host: string
  /** 子任务描述(标题用) */
  task: string
  role?: string
  /** _SA_STATE 分片 key */
  k: string
  /** 在分片里的下标 */
  idx: number
}

export const AgentNav = React.createContext<{
  open: (r: AgentRef) => void
  /** 打开"最近的子代理"列表页(跑完之后重新进来的入口) */
  openList: () => void
  /** 列表页里打开某个记录时, 详情页的返回键要说明回到哪 */
  inList: () => boolean
}>({ open: () => {}, openList: () => {}, inList: () => false })
