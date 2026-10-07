import { type KeyboardEvent, type MouseEvent, type ReactNode, useEffect, useState } from 'react'
import clsx from 'clsx'
import { IconChevronDownOutline14 } from './icons/index.tsx'
import css from './DisclosureRow.module.css'
import { SpringFold } from '../motion'

/** Shared 24px disclosure chrome for compact flow rows. */
export interface DisclosureRowProps {
  icon: ReactNode
  title: string
  open: boolean
  expandable: boolean
  onToggle: () => void
  /** Makes the complete title row the disclosure target. */
  expandOnRowClick?: boolean | undefined
  /** Replaces the collapsed icon with a chevron while the row is hovered. */
  previewChevron?: boolean | undefined
  /** Keeps `collapsedContent` inline while open. */
  keepContentWhenOpen?: boolean | undefined
  collapsedContent?: ReactNode
  children?: ReactNode
  className?: string | undefined
  rowClassName?: string | undefined
  leadingClassName?: string | undefined
  chevronClassName?: string | undefined
  titleClassName?: string | undefined
}

/**
 * Render one disclosure header and its controlled expanded content.
 * @param props - Visual content, controlled state, and interaction policy.
 * @returns the disclosure row.
 */
export function DisclosureRow({
  icon,
  title,
  open,
  expandable,
  onToggle,
  expandOnRowClick = false,
  previewChevron = expandable,
  keepContentWhenOpen = false,
  collapsedContent,
  children,
  className,
  rowClassName,
  leadingClassName,
  chevronClassName,
  titleClassName,
}: DisclosureRowProps) {
  const rowExpands = expandable && expandOnRowClick
  const toggleFromLeading = (event: MouseEvent<HTMLButtonElement>) => {
    event.stopPropagation()
    onToggle()
  }
  const toggleFromKeyboard = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!rowExpands || (event.key !== 'Enter' && event.key !== ' ')) return
    event.preventDefault()
    onToggle()
  }
  const collapsedLeading = previewChevron
    ? (
      <>
        <span className={css.iconIdle}>{icon}</span>
        <IconChevronDownOutline14 className={clsx(chevronClassName, css.chevronHover)} />
      </>
    )
    : icon
  const leading = open
    ? <IconChevronDownOutline14 className={chevronClassName} />
    : collapsedLeading

  // 2026-09-20 老板「为什么是直接展开的 而不是慢慢展开带动画的」:
  //   原来这里就是 `{open && children}` —— 点开内容"啪"地出现, 一点过渡都没有。
  //   改用 index.css 里现成的 .fold(grid-template-rows 0fr→1fr), 展开/收起都平滑。
  //   everOpen: 从没展开过的行不渲染内容(几十条历史工具行各自一大段 <pre>, 白渲染太贵),
  //   展开过一次就留着, 这样收起时才有动画可放。
  const [everOpen, setEverOpen] = useState(open)
  useEffect(() => { if (open) setEverOpen(true) }, [open])

  return (
    <div className={clsx(css.root, className)} data-open={open || undefined}>
      <div
        className={clsx(css.row, rowClassName)}
        data-disclosure-row
        data-expandable={rowExpands || undefined}
        role={rowExpands ? 'button' : undefined}
        tabIndex={rowExpands ? 0 : undefined}
        aria-expanded={rowExpands ? open : undefined}
        onClick={rowExpands ? onToggle : undefined}
        onKeyDown={rowExpands ? toggleFromKeyboard : undefined}
      >
        {expandable && !rowExpands ? (
          <button
            type="button"
            className={clsx(css.leading, leadingClassName)}
            aria-expanded={open}
            onClick={toggleFromLeading}
          >
            {leading}
          </button>
        ) : (
          <span className={clsx(css.leading, leadingClassName)}>
            {leading}
          </span>
        )}
        <span className={clsx(css.title, titleClassName)}>{title}</span>
        {(keepContentWhenOpen || !open) && collapsedContent}
      </div>
      {everOpen ? (
        // 2026-09-30 老板「动画不行 我要求和他一样」→ 换成 <SpringFold>:
        //   和 RikkaHub 的 animateContentSize(defaultSpatialSpec) 同一个东西 —— 逐帧写**真实高度**,
        //   弹簧 d=0.8/k=380, 展开带轻微过冲。原来的 grid-template-rows 0fr→1fr 是"匀速长开",
        //   没有回弹、也没有速度衔接(连点两下会顿)。
        <SpringFold open={open}>{children}</SpringFold>
      ) : null}
    </div>
  )
}
