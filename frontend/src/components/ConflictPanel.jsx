import RichText from './RichText.jsx'

const PRIORITY = {
  caliber_consistency: { zh: '口径一致性', rank: 1 },
  data_freshness: { zh: '数据新鲜度', rank: 2 },
  source_authority: { zh: '来源权威性', rank: 3 },
  sample_size: { zh: '样本量', rank: 4 },
}

const PRIORITY_ORDER = ['caliber_consistency', 'data_freshness', 'source_authority', 'sample_size']

export default function ConflictPanel({ run }) {
  const list = run.conclusion?.conflicts || []
  if (!list.length) return null

  return (
    <section id="conflict" className="card scroll-mt-16">
      <div className="card-pad border-b border-ink-300/60">
        <h2 className="text-[14px] font-semibold text-ink-900">
          证据冲突 —— {list.length} 处，逐条裁决，不做和稀泥
        </h2>
        <p className="mt-1 text-[11px] leading-relaxed text-ink-500">
          同一件事出现方向相反的证据时，写「综合来看影响中性」等于什么都没说。
          这里要求每条冲突必须交代：冲突的性质、按哪个维度裁决、裁决后的残余不确定性。
        </p>
      </div>

      <ol className="divide-y divide-ink-300/60">
        {list.map((c, i) => {
          const p = PRIORITY[c.resolving_priority] || { zh: c.resolving_priority, rank: 9 }
          return (
            <li key={i} className="card-pad">
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <span className="rounded bg-ink-900 px-1.5 py-0.5 font-mono text-[10px] text-white">
                  {c.sub_question_id}
                </span>
                <span className="text-[11px] text-ink-500">
                  涉及证据 {c.evidence_ids.join(' · ')}
                </span>
              </div>

              <Field title="冲突的性质" body={c.nature} />

              <div className="mt-2.5 rounded-md border border-ink-300/70 bg-ink-100/50 px-3 py-2.5">
                <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
                  <span className="label">裁决优先级</span>
                  <span className="rounded bg-white px-1.5 py-0.5 text-[10px] font-medium text-ink-700 ring-1 ring-ink-300/70">
                    {p.zh}
                  </span>
                  <PriorityBar active={c.resolving_priority} />
                </div>
                <Field title="裁决过程" body={c.resolution} dense />
              </div>

              <div className="mt-2.5 rounded-md border border-amber-200 bg-amber-50/70 px-3 py-2.5">
                <Field title="残余不确定性" body={c.residual_uncertainty} dense />
              </div>
            </li>
          )
        })}
      </ol>
    </section>
  )
}

// 把「固定优先级」画出来，而不是只说一句「按优先级裁决」——
// 读者要能看到这条冲突是在哪个维度上被判的，以及它跳过了哪些维度。
function PriorityBar({ active }) {
  return (
    <span className="flex items-center gap-0.5">
      {PRIORITY_ORDER.map((k) => (
        <span
          key={k}
          title={PRIORITY[k].zh}
          className={`h-1.5 w-5 rounded-sm ${
            k === active ? 'bg-ink-900' : 'bg-ink-300'
          }`}
        />
      ))}
      <span className="ml-1 text-[10px] text-ink-500">1 → 4</span>
    </span>
  )
}

function Field({ title, body, dense }) {
  return (
    <div className={dense ? '' : 'mt-1'}>
      <div className="label mb-1">{title}</div>
      <p className="text-[12px] leading-relaxed text-ink-700">
        <RichText text={body} />
      </p>
    </div>
  )
}
