import { useState } from 'react'
import RichText from './RichText.jsx'
import { CONFIDENCE, verdictOf, unvOf } from '../lib/ui.js'

export default function EvidenceGrid({ run }) {
  const [filter, setFilter] = useState('all')
  const all = run.evidence || []
  const shown = filter === 'all' ? all : all.filter((e) => e.verdict === filter)

  if (!all.length) return null

  const count = (k) => all.filter((e) => e.verdict === k).length

  return (
    <section id="evidence" className="scroll-mt-16">
      <div className="mb-2.5 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-[14px] font-semibold text-ink-900">
          证据卡 —— 每张卡都能回到原始返回
        </h2>
        <div className="flex flex-wrap gap-1.5">
          {[
            ['all', `全部 ${all.length}`],
            ['support', `支持 ${count('support')}`],
            ['refute', `反对 ${count('refute')}`],
            ['unverifiable', `无法验证 ${count('unverifiable')}`],
          ].map(([k, label]) => (
            <button
              key={k}
              onClick={() => setFilter(k)}
              className={`chip ${
                filter === k
                  ? 'border-ink-900 bg-ink-900 text-white'
                  : 'border-ink-300 bg-white text-ink-700 hover:bg-ink-100'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        {shown.map((e) => (
          <EvidenceCard key={e.id} e={e} />
        ))}
      </div>
    </section>
  )
}

function EvidenceCard({ e }) {
  const m = verdictOf(e.verdict)
  const unv = e.unverifiable
  const cat = unv ? unvOf(unv.category) : null

  return (
    <article className={`card border ${m.cls.replace(/bg-\S+/, '')} flex flex-col`}>
      <div className={`flex items-start justify-between gap-3 rounded-t-lg px-4 py-2.5 ${m.cls}`}>
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-mono text-[11px] opacity-70">{e.id}</span>
            <span className="font-medium">{m.zh}</span>
            <span className="text-[11px] opacity-70">置信度 {CONFIDENCE[e.confidence]}</span>
          </div>
        </div>
        <div className="shrink-0 text-right font-mono text-[14px] font-semibold">
          {e.display_value}
        </div>
      </div>

      <div className="card-pad flex-1 space-y-3">
        <p className="text-[13px] font-medium leading-snug text-ink-900">{e.claim}</p>

        <p className="text-[12px] leading-relaxed text-ink-700">
          <RichText text={e.reasoning} />
        </p>

        {unv && (
          // 「无法验证」不是一句「数据缺失」。这里强制展开三问，
          // 并把「本数据源根本没有」和「暂时取不到」区分开。
          <div className="rounded-md border border-ink-300/70 bg-unv-bg px-3 py-2.5">
            <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
              <span className="rounded bg-white px-1.5 py-0.5 text-[10px] font-medium text-ink-700 ring-1 ring-ink-300/70">
                {cat.zh}
              </span>
              <span
                className={`text-[10px] ${
                  cat.tone === 'hard' ? 'text-ref-fg' : 'text-ink-500'
                }`}
              >
                {cat.tone === 'hard'
                  ? '能力边界 —— 换数据源之前无法补齐'
                  : cat.tone === 'soft'
                    ? '可得性问题 —— 有路径补齐'
                    : '数据没问题，是判定规则在此区间不表态'}
              </span>
            </div>
            <dl className="space-y-1">
              <Row k="需要什么" v={unv.what_is_needed} />
              <Row k="从哪获得" v={unv.where_to_get} />
              <Row k="失败证据" v={unv.failure_evidence} />
            </dl>
          </div>
        )}

        <details className="group">
          <summary className="cursor-pointer text-[11px] text-ink-500 hover:text-ink-900">
            溯源与判定规则
          </summary>
          <dl className="mt-2 space-y-1 border-t border-ink-300/50 pt-2">
            <Row k="来源" v={e.provenance.source} />
            <Row k="报告期" v={e.provenance.report_period} />
            <Row k="口径" v={e.provenance.caliber} />
            <Row k="单位" v={e.provenance.unit} />
            <Row k="接口" v={e.provenance.endpoint} mono />
            <Row k="request_id" v={e.provenance.request_id || '—'} mono />
            <Row k="抓取时间" v={String(e.provenance.fetched_at).slice(0, 19).replace('T', ' ')} />
            <Row k="判定规则" v={e.decision_rule_applied} />
            <Row k="阈值" v={e.threshold_applied} mono />
          </dl>
        </details>
      </div>
    </article>
  )
}

function Row({ k, v, mono }) {
  return (
    <div className="flex gap-2">
      <dt className="kv-key w-[62px]">{k}</dt>
      <dd className={`kv-val ${mono ? 'font-mono text-[11px]' : ''}`}>{v ?? '—'}</dd>
    </div>
  )
}
