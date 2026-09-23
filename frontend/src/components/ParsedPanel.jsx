import RichText from './RichText.jsx'
import { THESIS_TYPE } from '../lib/ui.js'

export default function ParsedPanel({ run }) {
  const p = run.parsed
  const t = THESIS_TYPE[p.thesis_type] || { zh: p.thesis_type, desc: '' }

  return (
    <section id="parse" className="card scroll-mt-16">
      <div className="card-pad border-b border-ink-300/60">
        <h2 className="text-[14px] font-semibold text-ink-900">命题修订 —— 从大白话到可验证命题</h2>
        <p className="mt-1 text-[11px] leading-relaxed text-ink-500">
          原命题通常是模糊的。这里先把它归类、补齐时间窗与判定口径，再给出修订后的版本；
          改了什么、为什么改，逐条列出，不暗改。
        </p>
      </div>

      <div className="card-pad space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="chip border-ink-300 bg-ink-100 text-ink-700">{t.zh}</span>
          <span className="text-[11px] text-ink-500">{t.desc}</span>
          <span className="text-[11px] text-ink-500">
            标的：<span className="font-mono text-ink-700">{p.thscode || '未确定'}</span>
            {p.name ? ` · ${p.name}` : ''}
          </span>
        </div>

        <p className="text-[12px] leading-relaxed text-ink-700">
          <RichText text={p.type_rationale} />
        </p>

        <div className="grid gap-2 md:grid-cols-2">
          <VersionBox title="v1 · 你的原话" body={p.v1.text} sub={p.v1.horizon} muted />
          <VersionBox title="v2 · 修订后可验证版本" body={p.v2.text} sub={p.v2.horizon} />
        </div>

        {p.diffs?.length > 0 && (
          <div>
            <div className="label mb-1.5">修订差异</div>
            <ul className="space-y-1.5">
              {p.diffs.map((d, i) => (
                <li key={i} className="rounded-md border border-ink-300/70 px-3 py-2">
                  <div className="text-[12px]">
                    <span className="font-mono text-[11px] text-ink-500">{d.field}</span>
                    <div className="mt-1 flex flex-wrap items-center gap-1.5">
                      <span className="rounded bg-ref-bg px-1.5 py-0.5 text-[11px] text-ref-fg line-through">
                        <RichText text={d.before || '（空）'} />
                      </span>
                      <span className="text-ink-300">→</span>
                      <span className="rounded bg-sup-bg px-1.5 py-0.5 text-[11px] text-sup-fg">
                        <RichText text={d.after || '（空）'} />
                      </span>
                    </div>
                  </div>
                  <RichText
                    className="mt-1.5 block text-[11px] leading-relaxed text-ink-500"
                    text={d.reason}
                  />
                </li>
              ))}
            </ul>
          </div>
        )}

        {p.clarifications?.length > 0 && (
          <div>
            <div className="label mb-1.5">
              澄清问题 —— 产品会先问清楚，问不到就用下面明写的默认假设继续
            </div>
            <ul className="space-y-2">
              {p.clarifications.map((c, i) => (
                <li key={i} className="rounded-md bg-ink-100/60 px-3 py-2">
                  <RichText
                    className="block text-[12px] font-medium text-ink-900"
                    text={c.question}
                  />
                  <div className="mt-1 text-[11px] leading-relaxed text-ink-500">
                    为什么重要：<RichText text={c.why_it_matters} />
                  </div>
                  {c.assumption && (
                    <div className="mt-1 rounded bg-amber-50 px-2 py-1 text-[11px] leading-relaxed text-amber-800">
                      默认假设：<RichText text={c.assumption} />
                    </div>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}

        <p className="text-[11px] text-ink-500">
          拆解来源：<span className="font-mono">{run.decomposition?.generated_by}</span>
          {' · '}子问题来自手工模板（templates/gold.py），AI 只做实例化，不发明子问题、不产生数字
        </p>
      </div>
    </section>
  )
}

function VersionBox({ title, body, sub, muted }) {
  return (
    <div
      className={`rounded-md border px-3 py-2.5 ${
        muted ? 'border-ink-300/70 bg-ink-100/50' : 'border-ink-900/20 bg-white'
      }`}
    >
      <div className="label mb-1">{title}</div>
      <RichText
        className={`block text-[12px] leading-relaxed ${muted ? 'text-ink-500' : 'text-ink-900'}`}
        text={body}
      />
      <p className="mt-1 text-[11px] text-ink-500">
        时间窗：<RichText text={sub} />
      </p>
    </div>
  )
}
