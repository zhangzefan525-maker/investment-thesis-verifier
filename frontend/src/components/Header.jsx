import { THESIS_TYPE, verdictOf } from '../lib/ui.js'

export default function Header({ health, run }) {
  const live = health?.live_available
  const mode = run?.data_mode
  return (
    <header className="border-b border-ink-300/70 bg-white">
      <div className="mx-auto max-w-[1180px] px-6 py-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-[17px] font-semibold tracking-tight text-ink-900">
              投资命题多证据验证器
            </h1>
            <p className="mt-1 text-[12px] text-ink-500">
              把主观命题拆成可证伪的子问题，逐条标注支持 / 反对 / 无法验证
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {/* 数据模式必须显眼。实时与冻结快照给出的数字含义不同，
                不标出来就等于让读者把快照当实时行情。 */}
            <Badge
              tone={mode === 'live' ? 'live' : mode === 'fixture' ? 'snapshot' : 'idle'}
              text={
                mode === 'live'
                  ? '实时数据 · 同花顺扶摇'
                  : mode === 'fixture'
                    ? '冻结快照 · 非实时'
                    : live === undefined
                      ? '连接中…'
                      : live
                        ? '凭据就绪 · 待验证'
                        : '无凭据 · 走快照'
              }
            />
            {run && (
              <Badge
                tone="plain"
                text={`${THESIS_TYPE[run.parsed.thesis_type]?.zh || run.parsed.thesis_type} · ${
                  run.parsed.name || run.parsed.thscode || '标的未确定'
                }`}
              />
            )}
            {run?.conclusion && (
              <span
                className={`chip ${verdictOf(run.conclusion.verdict).cls}`}
                title={verdictOf(run.conclusion.verdict).full}
              >
                <i className={`h-1.5 w-1.5 rounded-full ${verdictOf(run.conclusion.verdict).dot}`} />
                {verdictOf(run.conclusion.verdict).full}
              </span>
            )}
          </div>
        </div>

        {run?.data_mode_note && (
          <p className="mt-2 text-[11px] leading-relaxed text-ink-500">{run.data_mode_note}</p>
        )}
      </div>
    </header>
  )
}

function Badge({ tone, text }) {
  const toneCls = {
    live: 'border-sup-line bg-sup-bg text-sup-fg',
    snapshot: 'border-amber-300 bg-amber-50 text-amber-800',
    idle: 'border-ink-300 bg-ink-100 text-ink-500',
    plain: 'border-ink-300 bg-white text-ink-700',
  }[tone]
  return <span className={`chip ${toneCls}`}>{text}</span>
}
