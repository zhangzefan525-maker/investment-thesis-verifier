// 三态与类型的展示映射。整个 UI 的语义色只在这里定义一次，
// 避免同一个「支持」在不同组件里用了不同的绿。

export const VERDICT = {
  support: {
    key: 'support',
    zh: '支持',
    full: '现有证据支持',
    cls: 'bg-sup-bg border-sup-line text-sup-fg',
    dot: 'bg-sup-dot',
    text: 'text-sup-fg',
  },
  refute: {
    key: 'refute',
    zh: '反对',
    full: '现有证据不支持',
    cls: 'bg-ref-bg border-ref-line text-ref-fg',
    dot: 'bg-ref-dot',
    text: 'text-ref-fg',
  },
  unverifiable: {
    key: 'unverifiable',
    zh: '无法验证',
    full: '证据不足以判断',
    cls: 'bg-unv-bg border-unv-line text-unv-fg',
    dot: 'bg-unv-dot',
    text: 'text-unv-fg',
  },
}

export const verdictOf = (v) => VERDICT[v] || VERDICT.unverifiable

export const CONFIDENCE = { high: '高', medium: '中', low: '低' }

export const THESIS_TYPE = {
  divergence: { zh: '背离型', desc: '估值回落，但基本面并未恶化' },
  attribution: { zh: '归因型', desc: '盈利改善来自主营业务' },
  transmission: { zh: '传导型', desc: '外部变化影响产业链利润分配' },
}

// 「无法验证」的七种原因。UI 上必须显示原因分类，
// 否则读者会把「本数据源没有这类数据」误读成「暂时取不到」。
export const UNV_CATEGORY = {
  data_not_exist: { zh: '本数据源不含此类数据', tone: 'hard' },
  not_disclosed: { zh: '数据存在但未披露到该颗粒度', tone: 'hard' },
  caliber_mismatch: { zh: '数据存在但口径不可比', tone: 'hard' },
  period_not_due: { zh: '报告期尚未到来', tone: 'soft' },
  source_unreachable: { zh: '数据源调用失败', tone: 'soft' },
  permission_denied: { zh: '当前凭据无该权限', tone: 'soft' },
  inconclusive_range: { zh: '数据完整，但落在判定中性区间', tone: 'neutral' },
}

export const unvOf = (c) =>
  UNV_CATEGORY[c] || { zh: c || '未分类', tone: 'neutral' }

// 失败三态：能补齐 / 补不齐 / 数据本身没问题。用边框粗细而非新颜色区分。
export const LAYER_ZH = {
  revenue_growth: '收入增速',
  gross_margin: '毛利率',
  expense_ratio: '期间费用率',
  non_recurring: '非经常性损益',
  minority_interest: '少数股东损益',
  cash_quality: '利润现金含量',
  volume: '量',
  rate: '价',
  mix: '结构',
  fx: '汇率',
}

export const fmtTime = (iso) => {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

export const PRESETS = [
  {
    label: '背离型 · 中国平安',
    text: '我认为中国平安估值已经回落但基本面并没有恶化',
    note: '实测结论：现有证据支持',
  },
  {
    label: '背离型 · 贵州茅台',
    text: '我认为贵州茅台估值已经回落但基本面并没有恶化',
    note: '实测结论：不支持（增收不增利）',
  },
  {
    label: '归因型 · 宁德时代',
    text: '我认为宁德时代的盈利改善来自主营业务',
    note: '实测结论：不支持',
  },
  {
    label: '传导型 · 碳酸锂',
    text: '我认为碳酸锂价格上涨会挤压宁德时代的利润空间',
    note: '实测结论：证据不足（能力边界）',
  },
]
