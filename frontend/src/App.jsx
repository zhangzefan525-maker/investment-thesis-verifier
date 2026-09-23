import { useCallback, useEffect, useMemo, useState } from 'react'
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
import RichText from './components/RichText.jsx'
import { PRESETS } from './lib/ui.js'

const SECTIONS = [
  ['conclusion', '结论'],
  ['decompose', '拆解'],
  ['evidence', '证据'],
  ['charts', '图表'],
  ['conflict', '冲突'],
  ['falsify', '反转条件'],
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
    fetch('/api/health')
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(setHealth)
      .catch((e) => setHealth({ status: 'unreachable', _error: String(e) }))
  }, [])

  const submit = useCallback(async () => {
    if (!text.trim() || loading) return
    setLoading(true)
    setError(null)
    setRun(null)
    try {
      const r = await fetch('/api/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ raw_text: text.trim() }),
      })
      const body = await r.json()
      if (!r.ok) throw new Error(body?.detail || `HTTP ${r.status}`)
      setRun(body)
      setActive('conclusion')
    } catch (e) {
      setError(String(e.message || e))
    } finally {
      setLoading(false)
    }
  }, [text, loading])

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
              <FailurePanel run={run} />
              <ParsedPanel run={run} />
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
