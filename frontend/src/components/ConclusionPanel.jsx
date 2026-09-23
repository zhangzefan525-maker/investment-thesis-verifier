import RichText from './RichText.jsx'
import { VERDICT, verdictOf } from '../lib/ui.js'

export default function ConclusionPanel({ run }) {
  const c = run.conclusion
  if (!c) return null
  const v = verdictOf(c.verdict)

  return (
    <section id="conclusion" className="scroll-mt-16">
      <div className={`card overflow-hidden border ${v.cls.replace('bg-', 'border-l-4 bg-')}`}>
        <div className="card-pad">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <span className={`chip ${v.cls}`}>
              <i className={`h-1.5 w-1.5 rounded-full ${v.dot}`} />
              {v.full}
            </span>
            <span className="text-[11px] text-ink-500">
              覆盖度自评 —— 支持 {c.support_count} · 反对 {c.refute_count} · 无法验证{' '}
              {c.unverifiable_count}
            </span>
          </div>

          <p className="text-[15px] leading-[1.75] text-ink-900">
            <RichText text={c.statement} />
          </p>

          <p className="mt-3 border-t border-ink-300/60 pt-3 text-[12px] leading-relaxed text-ink-500">
            {c.coverage_note}
          </p>
        </div>

        {/* 三态计数条：一眼看出这份结论是建立在几条证据上、其中多少条其实是空的 */}
        <div className="grid grid-cols-3 divide-x divide-ink-300/60 border-t border-ink-300/60">
          {['support', 'refute', 'unverifiable'].map((k) => {
            const n =
              k === 'support'
                ? c.support_count
                : k === 'refute'
                  ? c.refute_count
                  : c.unverifiable_count
            const m = VERDICT[k]
            return (
              <div key={k} className="px-5 py-3">
                <div className="flex items-baseline gap-2">
                  <span className={`text-[22px] font-semibold leading-none ${m.text}`}>{n}</span>
                  <span className="text-[11px] text-ink-500">{m.zh}</span>
                </div>
                <div className="mt-2 h-1 w-full rounded-full bg-ink-100">
                  <div
                    className={`h-1 rounded-full ${m.dot}`}
                    style={{
                      width: `${
                        (n / Math.max(1, c.support_count + c.refute_count + c.unverifiable_count)) *
                        100
                      }%`,
                    }}
                  />
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {c.limitations?.length > 0 && (
        <details className="card card-pad mt-3">
          <summary className="cursor-pointer text-[12px] font-medium text-ink-700">
            已知边界与未做事项（{c.limitations.length} 条）
            <span className="ml-2 font-normal text-ink-500">
              这些不是免责声明，是这份结论的适用范围
            </span>
          </summary>
          <ul className="mt-3 space-y-1.5">
            {c.limitations.map((l, i) => (
              <li key={i} className="flex gap-2 text-[12px] leading-relaxed text-ink-700">
                <span className="shrink-0 text-ink-300">—</span>
                <RichText text={l} />
              </li>
            ))}
          </ul>
        </details>
      )}

      <p className="mt-2 px-1 text-[11px] leading-relaxed text-ink-500">{c.disclaimer}</p>
    </section>
  )
}
