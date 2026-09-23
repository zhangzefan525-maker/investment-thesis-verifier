import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import Header from './components/Header.jsx'
import ThesisInput from './components/ThesisInput.jsx'
import ParsedPanel from './components/ParsedPanel.jsx'
import DecompositionPanel from './components/DecompositionPanel.jsx'
import EvidenceGrid from './components/EvidenceGrid.jsx'
import ConflictPanel from './components/ConflictPanel.jsx'
import ConclusionPanel from './components/ConclusionPanel.jsx'
import FalsificationTable from './components/FalsificationTable.jsx'
import FailurePanel from './components/FailurePanel.jsx'
import Charts from './components/Charts.jsx'
import ResearchPanel from './components/ResearchPanel.jsx'
import RichText from './components/RichText.jsx'
import { PRESETS } from './lib/ui.js'
import { api } from './lib/api.js'

const SECTIONS = [
  ['conclusion', '结论'],
  ['decompose', '拆解'],
  ['evidence', '证据'],
  ['charts', '图表'],
  ['conflict', '冲突'],
  ['falsify', '反转条件'],
  ['research', '继续用'],
  ['parse', '命题修订'],
]

export default function App() {
  const [text, setText] = useState(PRESETS[0].text)
  const [health, setHealth] = useState(null)
  const [run, setRun] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [active, setActive] = useState('conclusion')

  useEffect(() => {
    fetch(api('/api/health'))
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(setHealth)
      .catch((e) => setHealth({ status: 'unreachable', _error: String(e) }))
  }, [])

  // 在途闸门。`loading` 是 state：同一个事件循环里连点两下，两次回调读到的
  // 都是**渲染时**那个 `false`，于是两个请求都发出去 —— 谁后回来谁的结论就留在屏幕上，
  // 而用户只点过一次「开始验证」。用 ref 挡住，它在两次回调之间是共享的。
  // （重跑按钮已由 `busy` 禁用，但那同样是 state，同一次渲染里的两下照样漏。）
  const inflight = useRef(false)

  // `given` 是对澄清问题的回答。空对象 = 不回答，走产品默认假设，
  // 与引入这个参数之前的请求体完全一致。
  //
  // `rawText` 默认取文本框当前内容；但按回答重跑时**必须传本轮命题原文**，见下。
  const verify = useCallback(
    async (given = {}, { keepPrevious = false, rawText = text } = {}) => {
      const raw = (rawText || '').trim()
      if (!raw || inflight.current) return
      inflight.current = true
      setLoading(true)
      setError(null)
      // 首次验证时清空旧结果；按回答重跑时保留，否则整页闪一下白，
      // 用户会以为自己点了「清空」。
      if (!keepPrevious) setRun(null)
      try {
        const r = await fetch(api('/api/verify'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ raw_text: raw, clarifications: given }),
        })
        const body = await r.json()
        if (!r.ok) throw new Error(body?.detail || `HTTP ${r.status}`)
        setRun(body)
        setActive('conclusion')
      } catch (e) {
        setError(String(e.message || e))
      } finally {
        inflight.current = false
        setLoading(false)
      }
    },
    [text],
  )

  const submit = useCallback(() => verify({}), [verify])

  // 按回答重跑，用的是**本轮正在展示的那句命题**（run.parsed.raw_text），
  // 而不是文本框里的当前内容。
  //
  // 这是一处会静默出错的默认行为：用户改完文本框、又点了「按我的回答重跑」，
  // 此前会把澄清回答套到那句**刚改过的、还没验证过的**命题上——
  // 屏幕上仍然出现一句熟悉的命题被验证过，回答却落到了别处，
  // 既不报错也没有任何提示。回答是用户针对上一轮那句命题给出的，
  // 两者必须绑定；想验证新写的那句，走「开始验证」。
  const rerunWithAnswers = useCallback(
    (given) =>
      verify(given, {
        keepPrevious: true,
        rawText: run?.parsed?.raw_text || text,
      }),
    [verify, run, text],
  )

  // 每个区块只在下游真的有内容时才渲染——空区块比没区块更让人困惑。
  const visible = useMemo(() => {
    if (!run) return []
    return SECTIONS.filter(([k]) => {
      if (k === 'conflict') return (run.conclusion?.conflicts?.length || 0) > 0
      if (k === 'charts') return !!run.charts
      if (k === 'falsify') return (run.conclusion?.falsification_conditions?.length || 0) > 0
      if (k === 'decompose') return (run.decomposition?.sub_questions?.length || 0) > 0
      return true
    })
  }, [run])

  return (
    <div className="min-h-screen">
      <Header health={health} run={run} />

      <main className="mx-auto max-w-[1180px] px-6 pb-24">
        <ThesisInput
          text={text}
          setText={setText}
          onSubmit={submit}
          loading={loading}
          error={error}
        />

        {run && (
          <>
            <nav className="sticky top-0 z-20 -mx-6 mb-5 border-b border-ink-300/70 bg-slate-50/95 px-6 py-2.5 backdrop-blur">
              <div className="flex flex-wrap gap-1.5">
                {visible.map(([k, label]) => (
                  <a
                    key={k}
                    href={`#${k}`}
                    onClick={() => setActive(k)}
                    className={`rounded px-2.5 py-1 text-[12px] transition-colors ${
                      active === k
                        ? 'bg-ink-900 text-white'
                        : 'text-ink-500 hover:bg-ink-100 hover:text-ink-900'
                    }`}
                  >
                    {label}
                  </a>
                ))}
              </div>
            </nav>

            <div className="space-y-5">
              <ConclusionPanel run={run} />
              <DecompositionPanel run={run} />
              <EvidenceGrid run={run} />
              <Charts run={run} />
              <ConflictPanel run={run} />
              <FalsificationTable run={run} />
              <ResearchPanel run={run} />
              <FailurePanel run={run} />
              <ParsedPanel
                run={run}
                onRerun={rerunWithAnswers}
                busy={loading}
                inputText={text}
                error={error}
              />
            </div>
          </>
        )}

        {!run && !loading && <EmptyState />}
      </main>
    </div>
  )
}

function EmptyState() {
  return (
    <div className="card card-pad mt-2 text-[13px] leading-relaxed text-ink-500">
      <p className="mb-2 text-ink-700">
        上面四条例题都可以直接点「开始验证」跑通全链路。它们分别命中三种不同的结论：
        支持、不支持、证据不足。
      </p>
      <RichText
        className="block"
        text="产品做的事只有一件：把一句主观命题拆成若干条**能被数据证伪**的子问题，
        然后老老实实地告诉你，每一条的证据指向哪一边、还是根本无从判断。
        它不出具买卖建议，也不给标的打分排名。"
      />
    </div>
  )
}
