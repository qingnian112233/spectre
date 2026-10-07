import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './index.css'
import './motion.css'
import { SPRING_CSS_VARS, M3, springDuration } from './motion'

/** 把弹簧缓动/时长注入 :root —— CSS 里的 transition/animation 直接 var() 取用,
 *  保证 CSS 动画和 JS 里的 <SpringFold> 走的是同一条弹簧(不会一个弹一个不弹)。 */
for (const [k, v] of Object.entries(SPRING_CSS_VARS)) {
  document.documentElement.style.setProperty(k, v)
}
document.documentElement.style.setProperty('--spring-spatial-ms', `${springDuration(M3.defaultSpatial)}ms`)
document.documentElement.style.setProperty('--spring-fast-spatial-ms', `${springDuration(M3.fastSpatial)}ms`)
document.documentElement.style.setProperty('--spring-effects-ms', `${springDuration(M3.defaultEffects)}ms`)

/** 2026-09-18 加错误边界: 之前 morphicons 的图标数据不合法 → React 抛错 → 整页白屏,
 *  老板只看到空白根本不知道哪坏了。现在任何渲染异常都在页面上显示出来, 不再是白屏。 */
class Boundary extends React.Component<{ children: React.ReactNode }, { err: string }> {
  state = { err: '' }
  static getDerivedStateFromError(e: any) {
    return { err: `${e?.name || 'Error'}: ${String(e?.message || e).slice(0, 300)}` }
  }
  render() {
    if (this.state.err) {
      return (
        <div style={{ padding: 16, fontFamily: 'system-ui', color: '#ec1313' }}>
          <div style={{ fontWeight: 600, marginBottom: 6 }}>页面渲染出错（不是网络问题）</div>
          <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12 }}>{this.state.err}</pre>
          <button style={{ marginTop: 10, padding: '6px 12px' }} onClick={() => location.reload()}>重载</button>
        </div>
      )
    }
    return this.props.children
  }
}

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <Boundary>
      <App />
    </Boundary>
  </React.StrictMode>,
)
