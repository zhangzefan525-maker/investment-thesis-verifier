/**
 * 澄清环节的浏览器验收：在真实 DOM 里点选项、重跑，再读结论。
 * Python 测试守的是返回值，这里守的是「用户能不能真的点到那个控件」。
 */
import { chromium } from 'playwright'

const BASE = process.env.UI_BASE || 'http://127.0.0.1:8000'
const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } })
const errors = []
page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))
page.on('pageerror', (e) => errors.push(String(e)))

let failed = 0
const check = (name, ok, extra = '') => {
  console.log(`  ${ok ? '✓' : '✗'} ${name}${extra ? ' — ' + extra : ''}`)
  if (!ok) failed++
}

await page.goto(BASE + '/', { waitUntil: 'networkidle' })
await page.fill('textarea', '我认为贵州茅台估值已经回落但基本面并没有恶化')
await page.getByRole('button', { name: /验证/ }).first().click()
await page.waitForSelector('#conclusion', { timeout: 180000 })

const before = await page.locator('#conclusion').innerText()

// 「命题修订」区块里的澄清问题
await page.locator('a[href="#parse"]').click()
await page.waitForTimeout(500)
const panel = page.locator('#parse')
// 背离型是 3 + 2 + 3 = 8 个选项。写成 >= 9 会永远失败——这类「凭印象写的下界」
// 就是本仓库测试说明里说的那种「文档会过期、实跑不会」的反面教材。
const OPTION_COUNT = 8
const got = await panel.locator('button[aria-pressed]').count()
check('澄清问题区块存在', got === OPTION_COUNT, `${got} 个选项按钮（期望 ${OPTION_COUNT}）`)

// 未回答时显示的是默认假设
check('未回答时显示默认假设', (await panel.getByText('默认假设：').count()) === 3)

// 重跑按钮初始不可用
const rerun = panel.getByRole('button', { name: /按我的回答重跑/ })
check('未改动时重跑按钮禁用', await rerun.isDisabled())

// 选「与同业比」
await panel.getByRole('button', { name: '与同业比' }).click()
await page.waitForTimeout(200)
check('选中后按钮可用', await rerun.isEnabled())
check('提示待生效条数', (await panel.innerText()).includes('有 1 条待生效'))

await rerun.click()
await page.waitForSelector('#parse', { timeout: 180000 })
await page.waitForFunction(
  () => !document.body.innerText.includes('正在重跑'),
  { timeout: 180000 },
)
await page.waitForTimeout(800)

const text = await page.locator('#parse').innerText()
check('本轮按你的回答计算', text.includes('本轮按你的回答计算：与同业比'))
check('显示它改变了什么', text.includes('它改变了什么：'))
check('v2 引用了用户原话', text.includes('与同业比'))

const after = await page.locator('#conclusion').innerText()
check('结论随回答改变', before !== after, after.split('\n').slice(0, 6).join(' / ').slice(0, 120))
check('结论不再断言估值确已回落', !after.includes('估值确已回落'))

// 回答已生效 → 按钮回到禁用（草稿与已生效一致）
check('重跑后按钮回到禁用', await page.locator('#parse').getByRole('button', { name: /按我的回答重跑/ }).isDisabled())

// 全页 DOM 不得有未渲染的 **
const stars = await page.evaluate(() => {
  const out = []
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
  for (let n = w.nextNode(); n; n = w.nextNode()) if (n.textContent.includes('**')) out.push(n.textContent.trim().slice(0, 60))
  return out
})
check('无 ** 泄漏', stars.length === 0, stars.join(' | '))

check('零控制台错误', errors.length === 0, errors.slice(0, 2).join(' | '))

// ---------------------------------------------------------------------------
// 第二段：clarifications 没有选项时，面板不许说「可以在这里回答」
//
// 配了 ANTHROPIC_API_KEY 时，解析层会用 LLM 就本句命题生成临时追问，那些追问
// options 为空 —— 它们不在预置问题表里，没有对应的取数与判据。此时面板若仍写着
// 「可以在这里回答」「改动上面的选项后可以重跑」，用户读到的是一句承诺、
// 屏幕上却一个可点的东西都没有。这与「并可在界面上修改后重跑」是同一类错。
//
// 这一段不吃 API key：拿一份真实返回改掉 options 再喂给页面，测的是真实渲染。
// ---------------------------------------------------------------------------

const live = await page.evaluate(async () => {
  const r = await fetch('/api/verify', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ raw_text: '我认为贵州茅台估值已经回落但基本面并没有恶化', clarifications: {} }),
  })
  return r.json()
})
for (const c of live.parsed.clarifications) {
  c.options = []
  c.answer = null
  c.impact = ''
}

await page.unroute('**/api/verify').catch(() => {})
await page.route('**/api/verify', (route) =>
  route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(live) }),
)

await page.goto(BASE + '/', { waitUntil: 'networkidle' })
await page.fill('textarea', '我认为贵州茅台估值已经回落但基本面并没有恶化')
await page.getByRole('button', { name: /验证/ }).first().click()
await page.waitForSelector('#conclusion', { timeout: 180000 })
await page.locator('a[href="#parse"]').click()
await page.waitForTimeout(500)

const nogap = await page.locator('#parse').innerText()
check('无选项时不再声称「可以在这里回答」', !nogap.includes('可以在这里回答'))
check('无选项时不再说「改动上面的选项后可以重跑」', !nogap.includes('改动上面的选项后可以重跑'))
check('无选项时说明为什么答不了', nogap.includes('本轮不能在这里作答'))
check('逐条说明该问不在预置问题表内', nogap.includes('不在预置问题表内'))
check(
  '无选项时不渲染那个永远禁用的重跑按钮',
  (await page.locator('#parse').getByRole('button', { name: /按我的回答重跑/ }).count()) === 0,
)
check('默认假设仍然明写', (await page.locator('#parse').getByText('默认假设：').count()) === 3)
check('这一段无控制台错误', errors.length === 0, errors.slice(0, 2).join(' | '))

await browser.close()
console.log(failed ? `\n澄清环节验收：${failed} 项失败` : '\n澄清环节验收：全部通过')
process.exit(failed ? 1 : 0)
