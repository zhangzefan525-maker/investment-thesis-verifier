import { Fragment, useState } from 'react'
import RichText from './RichText.jsx'

const IMPACT = {
  high: { zh: '高', cls: 'bg-ref-bg text-ref-fg border-ref-line', note: '该变量翻转，总结论跟着翻转' },
  medium: { zh: '中', cls: 'bg-amber-50 text-amber-800 border-amber-300', note: '会削弱结论，但未必翻转' },
  low: { zh: '低', cls: 'bg-unv-bg text-unv-fg border-unv-line', note: '只影响局部判断' },
}

// 「已触发」标记。**刻意不用 sup / ref / unv 三色**：那三色在本产品里只表示
// 「证据指向哪一边」，一旦被借来表示「这个条件已经满足」，颜色就变成多义的，
// 而读者是靠着三种颜色秒读整页方向的。这里用一个中性的深色底——它是一个状态标记，
// 不是一个方向判断。
const LATCHED_NOTE =
  '该行的当前值**已经越过**它自己的触发阈值——也就是说，这里写的不再是「未来可能发生的风险」，' +
  '而是一件已经发生、并已计入上方结论的事。'

export default function FalsificationTable({ run }) {
  const list = run.conclusion?.falsification_conditions || []
  const [open, setOpen] = useState(null)
  if (!list.length) return null

  return (
    <section id="falsify" className="card scroll-mt-16">
      <div className="card-pad border-b border-ink-300/60">
        <h2 className="text-[14px] font-semibold text-ink-900">
          反转条件表 —— 什么信息变了，结论就得改
        </h2>
        <RichText
          className="mt-1 block text-[11px] leading-relaxed text-ink-500"
          text="每一条都给出监控变量、当前值、触发阈值、**阈值的依据**和下次披露时点。
          没有「依据」这一栏，阈值就是拍脑袋；本产品不允许拍脑袋。"
        />
      </div>

      <div className="overflow-x-auto">
        <table className="w-full border-collapse">
          <thead>
            <tr>
              <th className="th min-w-[200px]">监控变量</th>
              <th className="th min-w-[150px]">当前值</th>
              <th className="th min-w-[160px]">触发阈值</th>
              <th className="th w-[84px]">方向</th>
              <th className="th min-w-[150px]">翻转哪个子问题</th>
              <th className="th w-[76px]">边际影响</th>
              <th className="th min-w-[130px]">下次披露</th>
            </tr>
          </thead>
          <tbody>
            {list.map((f, i) => {
              const imp = IMPACT[f.marginal_impact] || IMPACT.low
              const isOpen = open === i
              return (
                <Fragment key={i}>
                  <tr
                    onClick={() => setOpen(isOpen ? null : i)}
                    className="cursor-pointer hover:bg-ink-100/40"
                  >
                    <td className="td font-medium text-ink-900">
                      <RichText text={f.monitored_variable} />
                      {f.already_triggered && (
                        <span
                          className="chip ml-1 border border-ink-900 bg-ink-900 text-white"
                          title={LATCHED_NOTE}
                        >
                          已触发
                        </span>
                      )}
                    </td>
                    <td className="td font-mono text-[11px]">{f.current_value}</td>
                    <td className="td">
                      <RichText text={f.trigger_threshold} />
                    </td>
                    <td className="td text-[11px]">{f.direction}</td>
                    <td className="td text-[11px]">
                      <RichText text={f.flips_sub_question} />
                    </td>
                    <td className="td">
                      <span
                        className={`chip border ${imp.cls}`}
                        title={imp.note}
                      >
                        {imp.zh}
                      </span>
                    </td>
                    <td className="td text-[11px]">{f.next_disclosure}</td>
                  </tr>
                  {isOpen && (
                    <tr className="bg-ink-100/50">
                      <td className="td" colSpan={7}>
                        <div className="label mb-1">阈值依据 —— 为什么是这个数</div>
                        <RichText
                          className="block text-[12px] leading-relaxed text-ink-700"
                          text={f.threshold_basis}
                        />
                      </td>
                    </tr>
                  )}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      </div>

      <p className="card-pad border-t border-ink-300/60 text-[11px] text-ink-500">
        点击任意一行展开它的阈值依据。边际影响「高」意味着该变量一旦越过阈值，
        上面那条结论会整体翻转。
        {list.some((f) => f.already_triggered) && (
          <>
            {' '}
            <span className="chip mx-1 border border-ink-900 bg-ink-900 text-white">
              已触发
            </span>
            <RichText text="标记的行，是**当前值已经越过自己阈值**的行——它们讲的不是未来的风险，而是已经发生、并已计入上方结论的事。把这种行当成「还没触发的条件」来读，会高估结论的稳固程度。" />
          </>
        )}
      </p>
    </section>
  )
}
