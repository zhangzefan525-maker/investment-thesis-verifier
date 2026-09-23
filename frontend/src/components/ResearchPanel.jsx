import { useCallback, useEffect, useState } from 'react'
import RichText from './RichText.jsx'
import { verdictOf } from '../lib/ui.js'
import { api } from '../lib/api.js'

// 一次验证跑完之后，用户手上还剩三件事可做：换标的再比一遍、就某条证据追问、
// 把反转条件存成待办。三件事都在这里，不另开页面——它们都依赖当前这次运行的上下文。
const TABS = [
  ['compare', '横向比较', '同一条命题套用到多个标的，并列看差异'],
  ['followup', '追问', '就某条子问题或证据继续深挖'],
  ['tasks', '研究任务', '把反转条件表存下来，到点人工复核'],
]

export default function ResearchPanel({ run }) {
  const [tab, setTab] = useState('compare')
  return (
    <section id="research" className="card scroll-mt-16">
      <div className="card-pad border-b border-ink-300/60">
        <h2 className="text-[14px] font-semibold text-ink-900">
          继续用 —— 比较、追问、存成研究任务
        </h2>
        <RichText
          className="mt-1 block text-[11px] leading-relaxed text-ink-500"
          text="三件事共用本次运行的上下文。比较**只并列、不排序、不打分**——
          排序会把研究工具变成荐股工具，越过合规边界。"
        />
      </div>

      <div className="flex flex-wrap gap-1.5 border-b border-ink-300/60 px-5 py-2.5">
        {TABS.map(([k, label, hint]) => (
          <button
            key={k}
            type="button"
            onClick={() => setTab(k)}
            title={hint}
            className={`rounded px-2.5 py-1 text-[12px] transition-colors ${
              tab === k
                ? 'bg-ink-900 text-white'
                : 'text-ink-500 hover:bg-ink-100 hover:text-ink-900'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="card-pad">
        {tab === 'compare' && <Compare run={run} />}
        {tab === 'followup' && <FollowUp run={run} />}
        {tab === 'tasks' && <Tasks run={run} />}
      </div>
    </section>
  )
}

// --------------------------------------------------------------------------
// 横向比较
// --------------------------------------------------------------------------

function Compare({ run }) {
  // 默认拿当前命题 + 当前标的，用户再加别的标的。至少两个才比得起来。
  const [text, setText] = useState(run.parsed.raw_text)
  // 默认带上当前标的 + 两个对照标的，去重——当前标的本身就是 600519 时不能出现两次。
  const [codes, setCodes] = useState(
    [...new Set([run.parsed.thscode, '600519.SH', '300750.SZ'].filter(Boolean))].join('、'),
  )
  const [out, setOut] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)

  const submit = useCallback(async () => {
    const thscodes = codes
      .split(/[、,，\s]+/)
      .map((s) => s.trim())
      .filter(Boolean)
    if (thscodes.length < 2) return setErr('至少要给两个标的才比得起来。')
    if (thscodes.length > 5) return setErr('一次最多比 5 个标的。')
    setBusy(true)
    setErr(null)
    setOut(null)
    try {
      const r = await fetch(api('/api/compare'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ raw_text_template: text.trim(), thscodes }),
      })
      const body = await r.json()
      if (!r.ok) throw new Error(body?.detail || `HTTP ${r.status}`)
      setOut(body)
    } catch (e) {
      setErr(String(e.message || e))
    } finally {
      setBusy(false)
    }
  }, [text, codes])

  return (
    <div className="space-y-3">
      <label className="block">
        <span className="label">命题模板</span>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={2}
          className="mt-1 w-full resize-y rounded-md border border-ink-300 px-3 py-2 text-[12px] leading-relaxed outline-none focus:border-ink-900"
        />
      </label>
      <label className="block">
        <span className="label">标的（顿号或空格分隔，带交易所后缀，2–5 个）</span>
        <span className="mt-0.5 block text-[11px] leading-relaxed text-ink-500">
          标的以这里为准，命题模板里写的公司名会被逐个重新消歧——
          模板只提供句式，不决定比谁。
        </span>
        <input
          value={codes}
          onChange={(e) => setCodes(e.target.value)}
          className="mt-1 w-full rounded-md border border-ink-300 px-3 py-2 text-[12px] outline-none focus:border-ink-900"
        />
      </label>
      <div className="flex items-center gap-3">
        <button className="btn-ghost" onClick={submit} disabled={busy}>
          {busy ? '比较中…' : '开始比较'}
        </button>
        {err && <span className="text-[12px] text-ref-fg">{err}</span>}
      </div>

      {out && (
        <>
          <div className="overflow-x-auto rounded-md border border-ink-300/70">
            <table className="w-full border-collapse">
              <thead>
                <tr>
                  <th className="th">标的</th>
                  <th className="th">结论</th>
                  <th className="th">支持</th>
                  <th className="th">反对</th>
                  <th className="th">无法验证</th>
                  <th className="th">证据构成</th>
                </tr>
              </thead>
              <tbody>
                {out.results.map((r) => {
                  const V = verdictOf(r.verdict)
                  const total = r.counts.support + r.counts.refute + r.counts.unverifiable || 1
                  return (
                    <tr key={r.thscode}>
                      <td className="td whitespace-nowrap font-medium text-ink-900">
                        {r.name || '—'}
                        <span className="ml-1 text-ink-500">{r.thscode}</span>
                      </td>
                      <td className="td whitespace-nowrap">
                        <span className={`chip ${V.cls}`}>
                          <i className={`h-1.5 w-1.5 rounded-full ${V.dot}`} />
                          {V.full}
                        </span>
                      </td>
                      <td className="td">{r.counts.support}</td>
                      <td className="td">{r.counts.refute}</td>
                      <td className="td">{r.counts.unverifiable}</td>
                      <td className="td w-[220px]">
                        <div className="flex h-2 w-full overflow-hidden rounded-full bg-ink-100">
                          <i
                            className="bg-sup-dot"
                            style={{ width: `${(r.counts.support / total) * 100}%` }}
                          />
                          <i
                            className="bg-ref-dot"
                            style={{ width: `${(r.counts.refute / total) * 100}%` }}
                          />
                          <i
                            className="bg-unv-dot"
                            style={{ width: `${(r.counts.unverifiable / total) * 100}%` }}
                          />
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          <RichText className="block text-[11px] leading-relaxed text-ink-500" text={out.note} />
        </>
      )}
    </div>
  )
}

// --------------------------------------------------------------------------
// 追问
// --------------------------------------------------------------------------

function FollowUp({ run }) {
  const targets = [
    { id: 'conclusion', label: '结论', target: 'conclusion' },
    ...run.decomposition.sub_questions.map((s) => ({
      id: s.id,
      label: `${s.id} ${s.text}`,
      target: 'sub_question',
    })),
    ...run.evidence.map((e) => ({
      id: e.id,
      label: `${e.id} ${e.claim}`,
      target: 'evidence',
    })),
  ]
  const [sel, setSel] = useState(targets[0])
  const [out, setOut] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)

  const ask = useCallback(async () => {
    setBusy(true)
    setErr(null)
    setOut(null)
    try {
      const r = await fetch(api('/api/followup'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          run_id: run.run_id,
          target: sel.target,
          target_id: sel.id,
          question: `展开说明 ${sel.id}`,
        }),
      })
      const body = await r.json()
      if (!r.ok) throw new Error(body?.detail || `HTTP ${r.status}`)
      setOut(body)
    } catch (e) {
      setErr(String(e.message || e))
    } finally {
      setBusy(false)
    }
  }, [run.run_id, sel])

  return (
    <div className="space-y-3">
      <label className="block">
        <span className="label">追问对象</span>
        <select
          value={sel.id}
          onChange={(e) => setSel(targets.find((t) => t.id === e.target.value))}
          className="mt-1 w-full rounded-md border border-ink-300 bg-white px-3 py-2 text-[12px] outline-none focus:border-ink-900"
        >
          {targets.map((t) => (
            <option key={t.id} value={t.id}>
              {t.label.slice(0, 90)}
            </option>
          ))}
        </select>
      </label>
      <div className="flex items-center gap-3">
        <button className="btn-ghost" onClick={ask} disabled={busy}>
          {busy ? '展开中…' : '展开这一条'}
        </button>
        {err && <span className="text-[12px] text-ref-fg">{err}</span>}
      </div>

      {out && (
        <div className="rounded-md border border-ink-300/70 bg-ink-100/40 px-4 py-3">
          {/* 后端答案里带 **粗体** 标记，且是多行文本——走 RichText 保留换行，
              不能直接放进 <pre>，否则星号会原样显示给读者。 */}
          <RichText
            className="block whitespace-pre-wrap break-words text-[12px] leading-relaxed text-ink-700"
            text={out.answer}
          />
          <p className="mt-2 border-t border-ink-300/60 pt-2 text-[11px] text-ink-500">
            {out.scope_note}
          </p>
        </div>
      )}
    </div>
  )
}

// --------------------------------------------------------------------------
// 研究任务
// --------------------------------------------------------------------------

function Tasks({ run }) {
  const [saved, setSaved] = useState([])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [note, setNote] = useState(null)

  const load = useCallback(async () => {
    try {
      const r = await fetch(api('/api/tasks'))
      const body = await r.json()
      setSaved(body.tasks || [])
      setNote(body.note)
    } catch (e) {
      setErr(String(e.message || e))
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const save = useCallback(async () => {
    setBusy(true)
    setErr(null)
    try {
      const r = await fetch(api(`/api/save-task?run_id=${encodeURIComponent(run.run_id)}`), {
        method: 'POST',
      })
      const body = await r.json()
      if (!r.ok) throw new Error(body?.detail || `HTTP ${r.status}`)
      await load()
    } catch (e) {
      setErr(String(e.message || e))
    } finally {
      setBusy(false)
    }
  }, [run.run_id, load])

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3">
        <button className="btn-ghost" onClick={save} disabled={busy}>
          {busy ? '保存中…' : '把本次的反转条件存为研究任务'}
        </button>
        {err && <span className="text-[12px] text-ref-fg">{err}</span>}
      </div>

      {saved.length === 0 ? (
        <p className="text-[12px] text-ink-500">还没有已保存的研究任务。</p>
      ) : (
        <div className="space-y-2">
          {saved.map((t) => (
            <div key={t.task_id} className="rounded-md border border-ink-300/70 px-4 py-3">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <span className="text-[12px] font-medium text-ink-900">{t.title}</span>
                <span className="text-[11px] text-ink-500">
                  {t.thscode}　下次复核：{t.next_check}
                </span>
              </div>
              <ul className="mt-2 space-y-1">
                {t.falsification_conditions.map((c, i) => (
                  <li key={i} className="text-[11px] leading-relaxed text-ink-700">
                    <span className={c.marginal_impact === 'high' ? 'text-ref-fg' : 'text-ink-500'}>
                      [{c.marginal_impact === 'high' ? '高' : c.marginal_impact === 'medium' ? '中' : '低'}]
                    </span>{' '}
                    {c.monitored_variable}　当前 {c.current_value}　→　&#8203;
                    {/* 阈值文案与主表同源，里面允许出现 **粗体**（它就在后端的
                        MARKDOWN_FIELDS 清单里）。这里此前是裸渲染，主表那一份却走了 RichText——
                        同一段文案在两处渲染方式不同，等于两处各错一半。 */}
                    <RichText text={c.trigger_threshold} />
                    {/* 复核清单里更要标出来：这份清单是留给用户日后逐条回看的，
                        把「已经发生」写成「将来会触发」，等于让用户去盯一件已经完成的事。 */}
                    {c.already_triggered && (
                      <span className="chip ml-1 border border-ink-900 bg-ink-900 text-white">
                        已触发
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
      {note && <p className="text-[11px] leading-relaxed text-ink-500">{note}</p>}
    </div>
  )
}
