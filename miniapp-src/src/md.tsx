/** 富文本渲染: markdown → HTML(marked, MIT) → 消毒(DOMPurify, MIT) → 用 DSH 的 markdown token 上样式
 *
 *  2026-09-18 老板反馈"网页富文本乱": 之前当纯文本渲染, `**粗体**`/``` 代码块/表格 全是原文。
 *  现在按 TG 那边的观感渲染(标题/列表/代码块/表格/引用/链接), 代码块带边框和等宽字体。
 */
import * as React from 'react'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { TG } from './api'

marked.setOptions({ gfm: true, breaks: true })

export function Markdown({ text }: { text: string }) {
  const html = React.useMemo(() => {
    let t = String(text || '')
    // TG 的自定义表情标签: 只留里面的字符
    t = t.replace(/<tg-emoji[^>]*>/g, '').replace(/<\/tg-emoji>/g, '')
    try {
      const raw = marked.parse(t, { async: false }) as string
      return DOMPurify.sanitize(raw, { ADD_ATTR: ['target', 'rel'] })
    } catch {
      return DOMPurify.sanitize(t)
    }
  }, [text])

  const onClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const a = (e.target as HTMLElement)?.closest?.('a') as HTMLAnchorElement | null
    if (a?.href) {
      e.preventDefault()
      // Telegram 里用原生打开外部浏览器, 否则新标签
      try { (TG as any)?.openLink ? (TG as any).openLink(a.href) : window.open(a.href, '_blank', 'noopener') }
      catch { window.open(a.href, '_blank', 'noopener') }
    }
  }
  return <div className="md" onClick={onClick} dangerouslySetInnerHTML={{ __html: html }} />
}
