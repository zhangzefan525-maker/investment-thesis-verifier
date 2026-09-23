import { useEffect, useMemo, useState } from 'react'
import RichText from './RichText.jsx'
import { THESIS_TYPE } from '../lib/ui.js'

// 回答生效之后，标签要说的是**实际执行了什么**。
// 取值来自后端 `ClarificationQuestion.effect_kind`，与 ANSWER_EFFECTS 那张表同源。
const EFFECT_LABEL = {
  unverifiable: '本轮按你的回答改判：',
  drop: '本轮按你的回答移出本轮范围：',
  limitation: '本轮按你的回答追加了一条适用边界（判定与取数未变）：',
  none: '本轮采纳了你的回答（下游判定未因此改变）：',
}

export default function ParsedPanel({ run, onRerun, busy, inputText = '', error = null }) {
  const p = run.parsed
  const t = THESIS_TYPE[p.thesis_type] || { zh: p.thesis_type, desc: '' }

  // 文本框里的命题与本轮跑的那句是否已经不一致。
  // 「按我的回答重跑」用的是**本轮命题原文**（回答是冲着它给的），
  // 因此一旦用户在文本框里改了字，就必须说清楚：这次重跑验证的不是他刚写的那句。
  const staleInput = inputText.trim() !== (p.raw_text || '').trim()

  // `generated_by` 说它是怎么来的。三种取值对应三件不同的事实，
  // 不能让一句话盖住全部——尤其不能把「AI 没参与」说成「AI 参与了」。
  const GEN = {
    manual_gold_template: {
      zh: 'hand-written template',
      note: '子问题全部来自手工模板（templates/gold.py）。本轮**没有调用 AI**：命题的类型判定与标的抽取由规则引擎完成。',
    },
    llm_instantiated: {
      zh: 'llm instantiated',
      note: '子问题来自手工模板；本轮由 AI 提供类型判定与标的/时间窗。AI 不发明子问题、不产生数字。',
    },
    hybrid: {
      zh: 'hybrid',
      note: '子问题来自手工模板（templates/gold.py）；本轮由 AI 参与类型判定与标的/时间窗抽取（规则引擎并行校验）。AI 只做这一步，不发明子问题、不产生数字。',
    },
  }
  const gen = GEN[run.decomposition?.generated_by] || {
    zh: run.decomposition?.generated_by || '—',
    note: '拆解来源字段取到了未知取值，本面板不替它编一句解释。',
  }

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
                      id={`clar-q-${i}`}
                      text={c.question}
                    />
                    <div className="mt-1 text-[11px] leading-relaxed text-ink-500">
                      为什么重要：<RichText text={c.why_it_matters} />
                    </div>

                    {c.options?.length > 0 && (
                      /* 这组控件属于上面那条问题。用 role="group" + aria-labelledby 标注**这一组**，
                         而不是把 aria-labelledby 挂在每个按钮上——挂到按钮上会**顶掉按钮自身的
                         可访问名**（读屏听到的是整条问题，而不是「与同业比」这个选项），
                         而且以可访问名定位的自动化脚本会当场找不到那个按钮。 */
                      <div
                        role="group"
                        aria-labelledby={`clar-q-${i}`}
                        className="mt-1.5 flex flex-wrap items-center gap-1.5"
                      >
                        {c.options.map((o) => {
                          const on = picked === o
                          return (
                            <button
                              key={o}
                              type="button"
                              aria-pressed={on}
                              // 重跑途中禁止再改：重跑返回后草稿会被回填成服务端的结果，
                              // 中途那一下点击会被静默丢掉——用户以为改了，实际没改。
                              disabled={busy}
                              onClick={() =>
                                setDraft((d) => ({ ...d, [c.question]: on ? '' : o }))
                              }
                              className={`rounded border px-2 py-0.5 text-[11px] transition-colors disabled:opacity-40 ${
                                on
                                  ? 'border-ink-900 bg-ink-900 text-white'
                                  : 'border-ink-300 bg-white text-ink-700 hover:border-ink-900'
                              }`}
                            >
                              <RichText text={o} />
                            </button>
                          )
                        })}
                        {/* 自由输入。README 里承诺了「也可以自己写一句」，后端也确实支持
                            （自定义回答会被如实标注为「未改动任何子问题」并有测试守着），
                            但界面上此前根本没有这个入口——承诺在文档里，控件不在屏幕上。
                            只在**可作答**（有预置选项）的题目下渲染：LLM 临时生成的那些
                            追问不在预置表里，给它们一个输入框等于让人答了却没有任何下游。 */}
                        <input
                          type="text"
                          value={picked && !c.options.includes(picked) ? picked : ''}
                          disabled={busy}
                          aria-label="或自己写一句"
                          placeholder="或自己写一句…"
                          onChange={(e) =>
                            setDraft((d) => ({ ...d, [c.question]: e.target.value }))
                          }
                          className="w-48 rounded border border-ink-300 bg-white px-2 py-0.5 text-[11px] text-ink-900 placeholder:text-ink-500/70 focus:border-ink-900 focus:outline-none disabled:opacity-40"
                        />
                        {picked && (
                          <button
                            type="button"
                            disabled={busy}
                            onClick={() => setDraft((d) => ({ ...d, [c.question]: '' }))}
                            className="text-[11px] text-ink-500 underline decoration-dotted disabled:opacity-40"
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
                        混在一起，用户会以为改一下就立即生效了。

                        标签按 `effect_kind` 分派，不再一律写「本轮按你的回答计算」：
                        28 个选项里 13 个只追加一条适用边界、8 个什么都不改（合计 21 个），
                        **没有改变任何计算**，
                        而同一条下面那行「它改变了什么」写的正是「追加适用边界」——
                        一个卡片里两句话互相打脸。取值由判定层给出，与真正执行的动作同源。 */}
                    {live && (
                      <div className="mt-1.5 rounded bg-ink-100 px-2 py-1 text-[11px] leading-relaxed text-ink-700">
                        {(EFFECT_LABEL[c.effect_kind] || '本轮按你的回答：')}
                        <RichText text={live} />
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

            {/* 提交上来的回答一条都没对上本轮的题面时，必须在这里说一句。
                回答以**题面为键**，而题面由命题类型生成：用户在上面的文本框里改一句命题、
                或类型判定换了一类，整组题面就换掉，上一轮的回答会一条也对不上。
                此前后端静默丢弃（errors 里没有痕迹），按钮旁却写着「有 N 条待生效」——
                用户答了、产品没听见，却告诉他听见了。 */}
            {p.unmatched_answers?.length > 0 && (
              <div
                role="alert"
                className="mt-2 rounded border border-amber-200 bg-amber-50 px-2 py-1 text-[11px] leading-relaxed text-amber-800"
              >
                <RichText
                  text={
                    `有 ${p.unmatched_answers.length} 条回答对不上本轮的任何一道澄清问题，` +
                    `已按默认假设继续：${p.unmatched_answers.join('、')}。` +
                    `原因通常是命题被改动过（或类型判定换了），题面整组变掉 —— ` +
                    `澄清问题是按命题类型生成的，回答以题面为键。` +
                    `**本轮没有采用这些回答**；请按当前这一轮的题面重新选一次（或直接改上面的命题重新验证）。`
                  }
                />
              </div>
            )}

            {/* 本轮回答的**执行记录**。它此前被后端并进 `errors`，前端据此渲染成
                「失败透明清单 —— 本次有 N 条失败或提示」。回答生效不是失败：
                把「你选了「与同业比」，估值侧的证据卡按这个口径重写了」列在失败清单里，
                既吓人又不真（那一轮其实一条错都没有）。现在两者分开，
                这一段明确写出它**不是**失败清单。 */}
            {run.answer_journal?.length > 0 && (
              <div className="mt-2 rounded border border-ink-300/70 px-2 py-1.5">
                <div className="text-[11px] font-medium text-ink-700">
                  {/* 标题也必须过 RichText：直接写 `**不是**`，屏幕上就是两个星号。
                      整个产品的正文都走这个组件，绕过它的那一个节点就是「** 泄漏」。 */}
                  <RichText
                    text={`本轮回答的执行记录（${run.answer_journal.length} 条，**不是**失败清单）`}
                  />
                </div>
                <ul className="mt-1 space-y-0.5">
                  {run.answer_journal.map((line, i) => (
                    <li key={i} className="text-[11px] leading-relaxed text-ink-500">
                      · <RichText text={line} />
                    </li>
                  ))}
                </ul>
                <div className="mt-1 text-[11px] leading-relaxed text-ink-500">
                  <RichText
                    text={
                      '这里只记**你的回答做了什么**，不是失败清单 —— 回答生效会被记在这里，' +
                      '而失败另有清单（在页面下方）。上面每一条都已落到子问题与结论里，' +
                      '说完就作数，不需要你再确认一次。'
                    }
                  />
                </div>
              </div>
            )}

            {/* 没有可点的选项时，这一行整体不渲染 —— 一个永远禁用的按钮配一句
                「改动上面的选项后可以重跑」，比不显示更糟：它让用户去找那个
                并不存在的控件。 */}
            {onRerun && answerable && (
              <div className="mt-2 space-y-1.5">
                {staleInput && (
                  <div className="rounded bg-amber-50 px-2 py-1 text-[11px] leading-relaxed text-amber-800">
                    <RichText
                      text="上方文本框里的命题已被改动。**本次重跑验证的仍是本轮那句话**（澄清回答是针对它给的）；想验证新写的那句，请点上方的「开始验证」。"
                    />
                  </div>
                )}
                <div className="flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    disabled={busy || changed.length === 0}
                    onClick={() => onRerun(draft)}
                    className="rounded bg-ink-900 px-3 py-1.5 text-[12px] text-white transition-opacity disabled:opacity-40"
                  >
                    {busy ? '正在重跑…' : '按我的回答重跑'}
                  </button>
                  {/* 状态行要能被读屏播报：重跑是异步的，此前从「正在重跑」回到结果
                      没有任何可播报的变化，用读屏的人不知道它跑完了没有。 */}
                  <span className="text-[11px] text-ink-500" role="status" aria-live="polite">
                    {busy
                      ? '正在按你的回答重跑，结论与子问题会一并更新…'
                      : changed.length === 0
                        ? '改动上面的选项（或自己写一句）后可以重跑'
                        : `有 ${changed.length} 条待生效。重跑后由判定层逐条给出它的效果：` +
                          `改变了下游的会重算子问题与结论，没改变的会明确标注「未改动任何子问题」——` +
                          `不做近似口径的假动作。`}
                  </span>
                </div>
                {/* 失败必须出现在**用户动作发生的地方**。此前重跑失败只在页面顶部的
                    输入框旁显示一条错误，而那个位置离这里约 4800px、早已滚出视口：
                    用户点完按钮什么都没发生，也没有任何解释。 */}
                {error && (
                  <div
                    role="alert"
                    className="rounded border border-ref-line bg-ref-bg px-2 py-1 text-[11px] leading-relaxed text-ref-fg"
                  >
                    <RichText
                      text={`重跑失败：${error}。上面显示的结果仍是**上一次成功**的那一轮，没有被改动。`}
                    />
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* 这一行此前是一句写死的「拆解来源：hybrid · 子问题来自手工模板，
            AI 只做实例化」——`hybrid` 是后端写死的取值，没有凭据时也一样。
            现在按后端实际给出的取值分派解释，AI 没参与的运行就说没参与。 */}
        <p className="text-[11px] leading-relaxed text-ink-500">
          拆解来源：<span className="font-mono">{gen.zh}</span>
          {' · '}
          <RichText text={gen.note} />
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
