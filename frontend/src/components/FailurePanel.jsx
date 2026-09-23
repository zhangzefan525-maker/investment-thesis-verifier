import { useState } from 'react'
import RichText from './RichText.jsx'

export default function FailurePanel({ run }) {
  const errors = run.errors || []
  const [open, setOpen] = useState(errors.length > 0)

  // 没有失败时也要说一句「没有失败」。沉默会让人怀疑是藏起来了——
  // 而「失败必须透明」正是这个产品对数据源的硬要求。
  if (!errors.length) {
    return (
      <section className="rounded-md border border-sup-line bg-sup-bg px-4 py-2.5">
        <p className="text-[12px] text-sup-fg">
          本次取数与判定的失败清单为<span className="font-semibold">空</span> ——
          所有被调用的接口都正常返回，所有子问题都产出了明确三态（含「无法验证」）。
          本产品不做静默跳过：任何取数失败、口径冲突、执行器异常都会出现在这里。
        </p>
      </section>
    )
  }

  return (
    <section className="rounded-md border border-ref-line bg-ref-bg">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center justify-between px-4 py-2.5 text-left"
      >
        <span className="text-[12px] font-medium text-ref-fg">
          失败透明清单 —— 本次有 {errors.length} 条失败或提示
        </span>
        <span className="text-[11px] text-ref-fg/70">{open ? '收起' : '展开'}</span>
      </button>
      {open && (
        <ul className="space-y-1.5 border-t border-ref-line px-4 py-3">
          {errors.map((x, i) => (
            <li key={i} className="flex gap-2 text-[12px] leading-relaxed text-ref-fg">
              <span className="shrink-0 font-mono text-[10px] opacity-60">{i + 1}</span>
              <RichText text={x} />
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
