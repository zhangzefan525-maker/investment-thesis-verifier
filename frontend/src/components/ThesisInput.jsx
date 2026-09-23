import { PRESETS } from '../lib/ui.js'

export default function ThesisInput({ text, setText, onSubmit, loading, error }) {
  return (
    <section className="card card-pad my-5">
      <div className="mb-2 flex items-baseline justify-between">
        <label className="label">投资命题</label>
        <span className="text-[11px] text-ink-500">
          用一句大白话写下来即可。产品会先把它澄清、修订成可验证的版本（见「命题修订」）
        </span>
      </div>

      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') onSubmit()
        }}
        rows={2}
        placeholder="例：我认为 XX 的估值已经回落，但基本面并没有恶化"
        className="w-full resize-none rounded-md border border-ink-300 bg-white px-3 py-2.5
                   text-[14px] leading-relaxed text-ink-900 outline-none
                   placeholder:text-ink-300 focus:border-ink-500"
      />

      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        <span className="mr-1 text-[11px] text-ink-500">例题：</span>
        {PRESETS.map((p) => (
          <button
            key={p.label}
            type="button"
            onClick={() => setText(p.text)}
            title={p.note}
            className={`chip ${
              text === p.text
                ? 'border-ink-900 bg-ink-900 text-white'
                : 'border-ink-300 bg-white text-ink-700 hover:bg-ink-100'
            }`}
          >
            {p.label}
          </button>
        ))}
      </div>

      <div className="mt-3 flex items-center gap-3">
        <button className="btn-primary" onClick={onSubmit} disabled={loading || !text.trim()}>
          {loading ? '正在拆解与取证…' : '开始验证'}
        </button>
        <span className="text-[11px] text-ink-500">⌘/Ctrl + Enter 提交</span>
        {loading && (
          <span className="text-[12px] text-ink-500">
            拉取行情、三张报表、财务指标与估值快照，逐条判定中
          </span>
        )}
      </div>

      {error && (
        <div className="mt-3 rounded-md border border-ref-line bg-ref-bg px-3 py-2 text-[12px] text-ref-fg">
          请求失败：{error}
        </div>
      )}
    </section>
  )
}
