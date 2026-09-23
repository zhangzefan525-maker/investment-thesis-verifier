import { useMemo, useState } from 'react'

export default function Charts({ run }) {
  const c = run.charts
  if (!c) return null
  return (
    <section id="charts" className="scroll-mt-16">
      <h2 className="mb-2.5 text-[14px] font-semibold text-ink-900">
        图表 —— 同一批证据的另一种读法
      </h2>
      <p className="mb-3 text-[11px] leading-relaxed text-ink-500">
        图上的每一个点都来自上面某张证据卡，没有额外取数、没有插值、没有平滑。
        纵轴不截断、不做对数变换——截断纵轴是最常见的视觉误导手法。
      </p>
      <div className="grid gap-3 lg:grid-cols-2">
        {c.valuation && <ValuationChart d={c.valuation} />}
        {c.profit && <ProfitChart d={c.profit} />}
        {c.margin && <MarginChart d={c.margin} />}
      </div>
    </section>
  )
}

const W = 520
const H = 240
const PAD = { t: 18, r: 16, b: 28, l: 44 }

function useScale(seriesList, opts = {}) {
  return useMemo(() => {
    const vals = seriesList.flat().filter((v) => typeof v === 'number' && Number.isFinite(v))
    if (!vals.length) return null
    let lo = Math.min(...vals)
    let hi = Math.max(...vals)
    if (opts.includeZero) {
      lo = Math.min(lo, 0)
      hi = Math.max(hi, 0)
    }
    if (lo === hi) {
      lo -= 1
      hi += 1
    }
    const span = hi - lo
    lo -= span * 0.08
    hi += span * 0.08
    return { lo, hi }
  }, [JSON.stringify(seriesList), opts.includeZero])
}

const xAt = (i, n) => PAD.l + ((W - PAD.l - PAD.r) * i) / Math.max(1, n - 1)
const yAt = (v, sc) =>
  PAD.t + (H - PAD.t - PAD.b) * (1 - (v - sc.lo) / (sc.hi - sc.lo))

function Frame({ sc, ticks = 4, fmt = (v) => v.toFixed(1), children }) {
  const lines = Array.from({ length: ticks + 1 }, (_, i) => sc.lo + ((sc.hi - sc.lo) * i) / ticks)
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img">
      {lines.map((v, i) => (
        <g key={i}>
          <line
            x1={PAD.l}
            x2={W - PAD.r}
            y1={yAt(v, sc)}
            y2={yAt(v, sc)}
            stroke="#e2e8f0"
            strokeWidth="1"
          />
          <text x={PAD.l - 6} y={yAt(v, sc) + 3.5} textAnchor="end" fontSize="9" fill="#94a3b8">
            {fmt(v)}
          </text>
        </g>
      ))}
      {children}
    </svg>
  )
}

// 横轴刻度抽稀：标签重叠比没有标签更糟。
//
// 三件事同时做，否则时间长一点的日频序列上标签一定糊成一团：
//   1. 按 minGap 抽稀；
//   2. 末位刻度必留，但若它离前一个已选刻度太近，就挤掉前一个而不是两个都画；
//   3. 丢弃与上一个已画刻度同名的标签——日频序列按「年-月」截断后，
//      相邻采样点很容易落在同一个月，画出来就是「2026-06 2026-06」。
function XLabels({ labels, minGap = 46 }) {
  const n = labels.length
  if (!n) return null
  const span = W - PAD.l - PAD.r
  const pxPerIdx = span / Math.max(1, n - 1)
  const minIdxGap = Math.max(1, Math.round(minGap / pxPerIdx))

  // 贪心铺刻度：每放一个就至少跳 minIdxGap 个点。
  // 「任意两个标签的间距 ≥ minGap」因此是构造出来的，不是抽稀完碰运气——
  // 先按 n/maxTicks 求步长再强行补末位，会让末位与前一格只差半个步长而叠在一起。
  const picked = []
  for (let i = 0; i < n; i += minIdxGap) picked.push(i)

  // 末位刻度必留。注意它的锚点是 end（否则会越出右边界），比中间锚点整体左移半个
  // 标签宽，因此最后一对需要比其他对多留半个标签的余量——不然末位会压在前一格上。
  // 字号 9，数字与连字符约 0.5em 宽。
  const last = n - 1
  if (picked[picked.length - 1] !== last) picked.push(last)

  const halfLabel = (9 * 0.5 * Math.max(...labels.map((s) => String(s).length))) / 2
  const edgeGap = minIdxGap + Math.round(halfLabel / pxPerIdx)
  // 首末两格分别是 start / end 锚点（防止越出绘图区），比中间锚点各多占半个标签宽，
  // 与相邻格之间需要多留这半个标签的余量。挤掉的是相邻格，不是首末两格本身。
  while (picked.length >= 2 && last - picked[picked.length - 2] < edgeGap) {
    picked.splice(picked.length - 2, 1)
  }
  while (picked.length >= 2 && picked[1] - picked[0] < edgeGap) {
    picked.splice(1, 1)
  }

  const shown = []
  let prevLabel = null
  for (const i of picked) {
    const l = labels[i]
    if (i !== last && l === prevLabel) continue
    prevLabel = l
    shown.push(i)
  }

  return (
    <>
      {shown.map((i) => (
        <text
          key={i}
          x={xAt(i, n)}
          y={H - PAD.b + 13}
          textAnchor={i === 0 ? 'start' : i === last ? 'end' : 'middle'}
          fontSize="9"
          fill="#94a3b8"
        >
          {labels[i]}
        </text>
      ))}
    </>
  )
}

function CardShell({ title, subtitle, legend, children, footer }) {
  return (
    <div className="card">
      <div className="px-4 pt-3.5">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h3 className="text-[13px] font-semibold text-ink-900">{title}</h3>
          {legend}
        </div>
        {subtitle && (
          <p className="mt-0.5 text-[11px] leading-relaxed text-ink-500">{subtitle}</p>
        )}
      </div>
      <div className="px-2">{children}</div>
      {footer && (
        <p className="px-4 pb-3.5 pt-1 text-[11px] leading-relaxed text-ink-500">{footer}</p>
      )}
    </div>
  )
}

// --------------------------------------------------------------------------
// 估值分位带
// --------------------------------------------------------------------------

function ValuationChart({ d }) {
  const [hover, setHover] = useState(null)
  const vals = d.series.map((p) => p.value)
  const bandVals = Object.values(d.bands || {})
  const sc = useScale([vals, bandVals])
  if (!sc) return null

  const n = d.series.length
  const path = d.series
    .map((p, i) => `${i === 0 ? 'M' : 'L'}${xAt(i, n).toFixed(1)},${yAt(p.value, sc).toFixed(1)}`)
    .join(' ')

  const bandOrder = ['p10', 'p25', 'p50', 'p75', 'p90']
  const bandTone = {
    p10: '#a7f3d0',
    p25: '#bbf7d0',
    p50: '#e2e8f0',
    p75: '#fecdd3',
    p90: '#fda4af',
  }

  return (
    <CardShell
      title="估值分位带"
      subtitle={`${d.caliber}　·　共 ${n} 个交易日`}
      legend={
        <div className="flex flex-wrap items-center gap-2 text-[10px] text-ink-500">
          <span className="flex items-center gap-1">
            <i className="h-0.5 w-4 bg-ink-900" />重建 PE
          </span>
          {d.official_pe_ttm != null && (
            <span className="flex items-center gap-1">
              <i className="h-0 w-4 border-t border-dashed border-sup-dot" />
              官方 pe_ttm
            </span>
          )}
        </div>
      }
      footer={`${d.note}　当前 PE ${d.latest}，处于自身历史 ${d.percentile} 分位${
        d.relative_gap != null
          ? `；重建值与官方 pe_ttm 相对偏差 ${(d.relative_gap * 100).toFixed(1)}%`
          : ''
      }。`}
    >
      <Frame sc={sc} fmt={(v) => v.toFixed(0)}>
        {bandOrder.map((k) =>
          d.bands?.[k] == null ? null : (
            <line
              key={k}
              x1={PAD.l}
              x2={W - PAD.r}
              y1={yAt(d.bands[k], sc)}
              y2={yAt(d.bands[k], sc)}
              stroke={bandTone[k]}
              strokeWidth="1.5"
              strokeDasharray="4 3"
            />
          ),
        )}

        <path d={path} fill="none" stroke="#0f172a" strokeWidth="1.6" />

        {d.official_pe_ttm != null && (
          <line
            x1={PAD.l}
            x2={W - PAD.r}
            y1={yAt(d.official_pe_ttm, sc)}
            y2={yAt(d.official_pe_ttm, sc)}
            stroke="#16a34a"
            strokeWidth="1.2"
            strokeDasharray="5 4"
          />
        )}

        {/* 末端点：结论说的「当前分位」就是这一点 */}
        <circle cx={xAt(n - 1, n)} cy={yAt(d.latest, sc)} r="3.5" fill="#0f172a" />
        <text
          x={xAt(n - 1, n) - 4}
          y={yAt(d.latest, sc) - 7}
          textAnchor="end"
          fontSize="10"
          fontWeight="600"
          fill="#0f172a"
        >
          {d.latest}
        </text>

        <XLabels labels={d.series.map((p) => p.label.slice(0, 7))} />
      </Frame>
    </CardShell>
  )
}

// --------------------------------------------------------------------------
// 收入 vs 利润同比
// --------------------------------------------------------------------------

function ProfitChart({ d }) {
  const n = d.periods.length
  const all = [...d.revenue_yoy, ...d.profit_yoy]
  const sc = useScale([all], { includeZero: true })
  if (!sc) return null

  const slot = (W - PAD.l - PAD.r) / n
  const bw = Math.max(4, Math.min(14, slot / 3.2))
  const zeroY = yAt(0, sc)

  return (
    <CardShell
      title="收入同比 vs 归母净利同比"
      subtitle="同报告期、同口径。两条柱方向背离时就是「增收不增利」"
      legend={
        <div className="flex items-center gap-2 text-[10px] text-ink-500">
          <span className="flex items-center gap-1">
            <i className="h-2 w-2 rounded-sm bg-slate-400" />营收
          </span>
          <span className="flex items-center gap-1">
            <i className="h-2 w-2 rounded-sm bg-ink-900" />归母净利
          </span>
        </div>
      }
      footer="柱高为同比增速（%）。区间内最新一期即结论所用的报告期。"
    >
      <Frame sc={sc} fmt={(v) => `${v.toFixed(0)}%`}>
        <line x1={PAD.l} x2={W - PAD.r} y1={zeroY} y2={zeroY} stroke="#94a3b8" strokeWidth="1" />
        {d.periods.map((_, i) => {
          const cx = PAD.l + slot * (i + 0.5)
          const rv = d.revenue_yoy[i]
          const pv = d.profit_yoy[i]
          return (
            <g key={i}>
              {rv != null && (
                <rect
                  x={cx - bw - 1}
                  y={Math.min(zeroY, yAt(rv, sc))}
                  width={bw}
                  height={Math.max(1, Math.abs(yAt(rv, sc) - zeroY))}
                  fill="#94a3b8"
                />
              )}
              {pv != null && (
                <rect
                  x={cx + 1}
                  y={Math.min(zeroY, yAt(pv, sc))}
                  width={bw}
                  height={Math.max(1, Math.abs(yAt(pv, sc) - zeroY))}
                  fill="#0f172a"
                />
              )}
            </g>
          )
        })}
        <XLabels labels={d.periods} minGap={40} />
      </Frame>
    </CardShell>
  )
}

// --------------------------------------------------------------------------
// 毛利率与成本率
// --------------------------------------------------------------------------

function MarginChart({ d }) {
  const sc = useScale([d.gross_margin, d.cost_ratio])
  if (!sc) return null
  const n = d.periods.length
  const line = (arr, color, dash) => (
    <path
      d={arr
        .map((v, i) =>
          v == null ? '' : `${i === 0 || arr[i - 1] == null ? 'M' : 'L'}${xAt(i, n).toFixed(1)},${yAt(v, sc).toFixed(1)}`,
        )
        .filter(Boolean)
        .join(' ')}
      fill="none"
      stroke={color}
      strokeWidth="1.6"
      strokeDasharray={dash}
    />
  )

  return (
    <CardShell
      title="毛利率与营业成本率"
      subtitle="两者互为镜像（毛利率 = 1 − 成本率）。成本率抬升而毛利率同步下滑，说明成本压力由公司自行承担"
      legend={
        <div className="flex items-center gap-2 text-[10px] text-ink-500">
          <span className="flex items-center gap-1">
            <i className="h-0.5 w-4 bg-sup-dot" />毛利率
          </span>
          <span className="flex items-center gap-1">
            <i className="h-0.5 w-4 bg-ref-dot" />成本率
          </span>
        </div>
      }
      footer="两条线共用同一纵轴，因此可以直接比较间距；不做双轴——双轴会让两条无关的线看起来「贴合」或「背离」。"
    >
      <Frame sc={sc} fmt={(v) => `${v.toFixed(0)}%`}>
        {line(d.gross_margin, '#16a34a')}
        {line(d.cost_ratio, '#e11d48')}
        <XLabels labels={d.periods} minGap={46} />
      </Frame>
    </CardShell>
  )
}
