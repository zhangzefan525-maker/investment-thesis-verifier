import { LAYER_ZH, VERDICT, verdictOf } from '../lib/ui.js'
import RichText from './RichText.jsx'

export default function DecompositionPanel({ run }) {
  const d = run.decomposition
  const evBySq = Object.fromEntries((run.evidence || []).map((e) => [e.sub_question_id, e]))
  if (!d?.sub_questions?.length) return null

  return (
    <section id="decompose" className="card scroll-mt-16">
      <div className="card-pad border-b border-ink-300/60">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="text-[14px] font-semibold text-ink-900">
            子问题拆解 —— 每条都必须能被数据证伪
          </h2>
          <span className="text-[11px] text-ink-500">
            共 {d.sub_questions.length} 条 · 五字段（指标/数据源/时间窗/判定规则/阈值）缺一不可，
            缺了就只能进「无法拆解」清单
          </span>
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full border-collapse">
          <thead>
            <tr>
              <th className="th w-[52px]">编号</th>
              <th className="th min-w-[210px]">子问题</th>
              <th className="th min-w-[260px]">判定规则与阈值</th>
              <th className="th w-[86px]">期望方向</th>
              <th className="th w-[92px]">实际证据</th>
            </tr>
          </thead>
          <tbody>
            {d.sub_questions.map((s) => {
              const e = evBySq[s.id]
              const m = e ? verdictOf(e.verdict) : null
              const hit = e && e.verdict === s.expected_direction
              return (
                <tr key={s.id} className="hover:bg-ink-100/40">
                  <td className="td font-mono text-[11px] text-ink-500">{s.id}</td>
                  <td className="td">
                    {/* 模板里的子问题正文与判定规则带 **强调**。这些字段过去是裸渲染的，
                        于是「成本率必须**上升**」把星号原样显示给了读者——
                        后端写的 markdown 与前端认得的 markdown 必须是一套。 */}
                    <RichText className="block text-ink-900" text={s.text} />
                    {/* 表头写着「五字段缺一不可」，那五字段就得真的都看得见。
                        早先这里只显示了指标与判定规则，数据源、时间窗、以及
                        「为什么这条能验证命题」都只存在于 JSON 里——
                        读者无从判断这条子问题是不是拍脑袋想出来的。 */}
                    <div className="mt-1 space-y-0.5 text-[11px] leading-relaxed text-ink-500">
                      <div>
                        指标：<RichText text={s.metric} />
                        {s.layer && (
                          <span className="ml-2 rounded bg-ink-100 px-1.5 py-0.5">
                            {LAYER_ZH[s.layer] || s.layer}
                          </span>
                        )}
                      </div>
                      <div>
                        数据源：<RichText text={s.data_source} />
                      </div>
                      <div>
                        时间窗：<RichText text={s.time_window} />
                      </div>
                    </div>
                    {s.rationale && (
                      <div className="mt-1.5 border-t border-dashed border-ink-300/70 pt-1.5 text-[11px] leading-relaxed text-ink-500">
                        <span className="text-ink-700">为什么能验证：</span>
                        <RichText text={s.rationale} />
                      </div>
                    )}
                  </td>
                  <td className="td">
                    <RichText className="block" text={s.decision_rule} />
                    <div className="mt-1 font-mono text-[11px] text-ink-500">{s.threshold}</div>
                  </td>
                  <td className="td">
                    <span className={`chip ${verdictOf(s.expected_direction).cls}`}>
                      {verdictOf(s.expected_direction).zh}
                    </span>
                  </td>
                  <td className="td">
                    {m ? (
                      <div>
                        <span className={`chip ${m.cls}`}>
                          <i className={`h-1.5 w-1.5 rounded-full ${m.dot}`} />
                          {m.zh}
                        </span>
                        {/* 实际与期望不一致时点出来：这是「命题被证伪」的具象位置 */}
                        {!hit && (
                          <div className="mt-1 text-[10px] text-ink-500">
                            与期望方向相反
                          </div>
                        )}
                        <div className="mt-1 font-mono text-[11px] text-ink-700">
                          {e.display_value}
                        </div>
                      </div>
                    ) : (
                      <span className="text-[11px] text-ink-300">未产出</span>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <SkippedLayers d={d} />
    </section>
  )
}

// 做不到的分解层必须写在明面上。这是本项目对「维度跳过须显式声明」这条约束的兑现，
// 也是这个产品最容易被做砸的地方——不写出来，读者会以为「没提就是没问题」。
function SkippedLayers({ d }) {
  const skipped = d.skipped_layers || []
  const unable = d.unable_to_decompose || []
  if (!skipped.length && !unable.length) return null
  return (
    <div className="card-pad border-t border-ink-300/60 bg-unv-bg/60">
      <div className="label mb-2">显式声明的跳过项</div>

      {skipped.length > 0 && (
        <ul className="space-y-1.5">
          {skipped.map((l) => (
            <li key={l} className="flex gap-2 text-[12px] leading-relaxed">
              <span className="shrink-0 rounded bg-white px-1.5 py-0.5 font-mono text-[10px] text-ink-500 ring-1 ring-ink-300/70">
                {l}
              </span>
              <span className="text-ink-700">{d.skipped_layer_reasons?.[l] || '未说明原因'}</span>
            </li>
          ))}
        </ul>
      )}

      {unable.map((u, i) => (
        <div key={i} className="mt-2 text-[12px] leading-relaxed text-ink-700">
          <span className="font-medium">{u.attempted_question}</span>
          <span className="text-ink-500"> —— 缺字段「{u.missing_field}」：{u.reason}</span>
        </div>
      ))}

      <RichText
        className="mt-2 block text-[11px] leading-relaxed text-ink-500"
        text={unsupportedNote(skipped)}
      />
    </div>
  )
}

function unsupportedNote(skipped) {
  const volRate = ['volume', 'rate', 'mix', 'fx'].filter((l) => skipped.includes(l))
  if (volRate.length === 0) return '以上为本次未覆盖的分解层及其原因。'
  return (
    `其中 ${volRate.join(' / ')} 四层不是「本次没做」，而是在本数据源下**永久做不到**：` +
    `扶摇公开接口不提供产销量、分产品单价、分部收入与外币敞口。` +
    `写在这里是为了让读者知道，收入变化中「量」与「价」各占多少，本产品无法回答。`
  )
}
