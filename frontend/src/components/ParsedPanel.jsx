import { useEffect, useMemo, useState } from 'react'
import RichText from './RichText.jsx'
import { THESIS_TYPE } from '../lib/ui.js'

export default function ParsedPanel({ run, onRerun, busy }) {
  const p = run.parsed
  const t = THESIS_TYPE[p.thesis_type] || { zh: p.thesis_type, desc: '' }

  // 有没有**可作答**的澄清问题：判据是有没有选项。
  //
  // 这里不是一个多余的判断，而是一条曾经踩过的坑：配了 ANTHROPIC_API_KEY 时，
  // 解析层会用 LLM 就本句命题生成临时追问，那些追问的 options 是空的 ——
  // 它们不在预置问题表里，没有对应的取数与判据。而面板此前无条件地写着
  // 「可以在这里回答」「改动上面的选项后可以重跑」，用户看到的是一句承诺，
  // 屏幕上却一个可点的东西都没有。这与「并可在界面上修改后重跑」是同一类错。
  //
  // 做法是如实收口，而不是给这些追问硬套一条近似口径 —— 后者会更危险：
  // 用户会得到一个看着像结论的东西。
  const answerable = (p.clarifications || []).some((c) => c.options?.length > 0)

  // 本轮已经生效的回答。重跑之后新一轮的返回里带着它们，据此回填草稿 ——
  // 不回填的话，用户重跑一次就会看到自己的选择全部变回默认值，
  // 分会以为回答没被采纳，而实际上结论已经按它改过了。
  const applied = useMemo(() => {
    const seeded = {}
    for (const c of p.clarifications || []) if (c.answer) seeded[c.question] = c.answer
    return seeded
  }, [run.run_id])

  const [draft, setDraft] = useState(applied)
  useEffect(() => setDraft(applied), [applied])

  const changed = useMemo(() => {
    const keys = new Set([...Object.keys(draft), ...Object.keys(applied)])
    return [...keys].filter((k) => (draft[k] || '') !== (applied[k] || ''))
  }, [draft, applied])

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

        <p className="text-[11px] leading-relaxed text-ink-500">
          <RichText text={p.v2.decision_context} />
        </p>

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
              {answerable
                ? '澄清问题 —— 可以在这里回答；不回答就按下面明写的默认假设继续'
                : '澄清问题 —— 本轮不能在这里作答，原因见下'}
            </div>
            <ul className="space-y-2">
              {p.clarifications.map((c, i) => {
                const picked = draft[c.question] || ''
                const live = applied[c.question] || ''
                return (
                  <li key={i} className="rounded-md bg-ink-100/60 px-3 py-2">
                    <RichText
                      className="block text-[12px] font-medium text-ink-900"
                      text={c.question}
                    />
                    <div className="mt-1 text-[11px] leading-relaxed text-ink-500">
                      为什么重要：<RichText text={c.why_it_matters} />
                    </div>

                    {c.options?.length > 0 && (
                      <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                        {c.options.map((o) => {
                          const on = picked === o
                          return (
                            <button
                              key={o}
                              type="button"
                              aria-pressed={on}
                              onClick={() =>
                                setDraft((d) => ({ ...d, [c.question]: on ? '' : o }))
                              }
                              className={`rounded border px-2 py-0.5 text-[11px] transition-colors ${
                                on
                                  ? 'border-ink-900 bg-ink-900 text-white'
                                  : 'border-ink-300 bg-white text-ink-700 hover:border-ink-900'
                              }`}
                            >
                              <RichText text={o} />
                            </button>
                          )
                        })}
                        {picked && (
                          <button
                            type="button"
                            onClick={() => setDraft((d) => ({ ...d, [c.question]: '' }))}
                            className="text-[11px] text-ink-500 underline decoration-dotted"
                          >
                            取消这一条
                          </button>
                        )}
                      </div>
                    )}

                    {!c.options?.length && (
                      <div className="mt-1 text-[11px] leading-relaxed text-ink-500">
                        本问由 AI 按这句命题临时生成，不在预置问题表内，没有对应的取数与判据，
                        因此本轮无法作答 —— 硬答一个口径，比承认答不了更危险。
                      </div>
                    )}

                    {/* 本轮**实际生效**的取值。分成两条显示而不是一条，
                        是因为「你正在改的」和「结论是按哪个算的」必须能分辨 ——
                        混在一起，用户会以为改一下就立即生效了。 */}
                    {live && (
                      <div className="mt-1.5 rounded bg-sup-bg/70 px-2 py-1 text-[11px] leading-relaxed text-sup-fg">
                        本轮按你的回答计算：<RichText text={live} />
                      </div>
                    )}
                    {live && c.impact && (
                      <div className="mt-1 rounded bg-white px-2 py-1 text-[11px] leading-relaxed text-ink-700 ring-1 ring-ink-300/70">
                        它改变了什么：<RichText text={c.impact} />
                      </div>
                    )}
                    {!live && c.assumption && (
                      <div className="mt-1.5 rounded bg-amber-50 px-2 py-1 text-[11px] leading-relaxed text-amber-800">
                        默认假设：<RichText text={c.assumption} />
                      </div>
                    )}
                  </li>
                )
              })}
            </ul>

            {/* 没有可点的选项时，这一行整体不渲染 —— 一个永远禁用的按钮配一句
                「改动上面的选项后可以重跑」，比不显示更糟：它让用户去找那个
                并不存在的控件。 */}
            {onRerun && answerable && (
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  disabled={busy || changed.length === 0}
                  onClick={() => onRerun(draft)}
                  className="rounded bg-ink-900 px-3 py-1.5 text-[12px] text-white transition-opacity disabled:opacity-40"
                >
                  {busy ? '正在重跑…' : '按我的回答重跑'}
                </button>
                <span className="text-[11px] text-ink-500">
                  {changed.length === 0
                    ? '改动上面的选项后可以重跑，结论会按你的口径重算'
                    : `有 ${changed.length} 条待生效，重跑后子问题与结论会一并更新`}
                </span>
              </div>
            )}
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
