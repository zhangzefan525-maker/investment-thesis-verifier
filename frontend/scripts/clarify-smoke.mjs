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
// 标签必须说**实际执行了什么**。选「与同业比」的效果是把估值侧的证据卡改判为
// 无法验证（effect_kind = unverifiable），因此标签是「改判」。
// 此前这里无条件写着「本轮按你的回答**计算**：」——同一个选项的效果被
// 并排两行写成了互相打脸的两句话：标签说它改了计算，下面那行说它只是换了取数口径。
check('标签说的是实际执行的动作', text.includes('本轮按你的回答改判：与同业比'))
check('不再谎称改了计算', !text.includes('本轮按你的回答计算'))
check('显示它改变了什么', text.includes('它改变了什么：'))
check('v2 引用了用户原话', text.includes('与同业比'))

const after = await page.locator('#conclusion').innerText()
check('结论随回答改变', before !== after, after.split('\n').slice(0, 6).join(' / ').slice(0, 120))
check('结论不再断言估值确已回落', !after.includes('估值确已回落'))

// 依据被作废的图必须**真的从 DOM 里撤下来**并写明原因。图走的是另一条代码路径
// （只读取数结果），它不会自己知道某条证据已被澄清环节改判 —— 这条断言盯的
// 就是那两条路径没有再次分家：证据说「无法验证」，图却照旧印着一个分位。
const charts = await page.locator('#charts').innerText()
check('估值图已随证据撤下', charts.includes('已撤下：估值分位带图'), charts.slice(0, 80))
check('撤下原因写明是证据被判无效', charts.includes('改判为无法验证'))
check('撤下的图不再画出来', !charts.includes('重建 PE 序列') || charts.includes('已撤下'))

// 回答生效这件事必须出现在**用户做动作的地方**，并且如实说它不是失败。
// 它此前被后端并进 errors，前端渲染成「本次有 1 条失败或提示」——
// 那一轮其实一条错都没有，用户会因为自己点了一个选项而以为自己搞坏了什么。
const body = await page.locator('body').innerText()
check('回答的执行记录出现在面板里', text.includes('本轮回答的执行记录'))
check('执行记录明说它不是失败清单', text.includes('不是失败清单'))
check('执行记录写的是实际发生的事', text.includes('SQ-01 改判为无法验证'))
check('回答生效不再被算作失败', !body.includes('本次有 1 条失败或提示'))
check('失败清单如实为空', body.includes('本次取数与判定的失败清单为'))

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

// ---------------------------------------------------------------------------
// 第三段：回答必须落在它自己那句命题上，以及几处「说了它没做的事」
//
// 这一段的每一条都对应一处实测过的缺陷：
//   ① 改了文本框再点重跑 → 回答被套到另一句命题上（不报错）；
//   ② README 承诺「也可以自己写一句」，界面上没有那个输入框；
//   ③ 重跑失败只在页面顶部报错，而那里离按钮约 4800px、早已滚出视口；
//   ④ 重跑途中还能继续点选项，那一下会被静默丢弃；
//   ⑤ 「拆解来源」写死成 hybrid，没凭据时 AI 一个字都没参与也这么说。
// ---------------------------------------------------------------------------

await page.unroute('**/api/verify').catch(() => {})
const posted = []
page.on('request', (r) => {
  if (r.url().includes('/api/verify') && r.method() === 'POST') posted.push(r.postData() || '')
})

const PROPOSITION = '我认为贵州茅台估值已经回落但基本面并没有恶化'
await page.goto(BASE + '/', { waitUntil: 'networkidle' })
await page.fill('textarea', PROPOSITION)
await page.getByRole('button', { name: /验证/ }).first().click()
await page.waitForSelector('#conclusion', { timeout: 180000 })
await page.locator('a[href="#parse"]').click()
await page.waitForTimeout(500)

const p3 = page.locator('#parse')

// ③ 拆解来源：没有凭据的一次运行里，AI 一个字都没参与，界面不能说是 hybrid。
const sourceLine = await p3.innerText()
check('拆解来源不冒充 AI 参与', sourceLine.includes('没有调用 AI'), sourceLine.includes('hybrid') ? '仍写着 hybrid' : '')
check('拆解来源不再写死 hybrid', !sourceLine.includes('hybrid'))

// ② 自由输入：README 承诺了「也可以自己写一句」，界面上就得有那个控件。
const customInputs = p3.locator('input[placeholder="或自己写一句…"]')
check('每题都有自由输入框', (await customInputs.count()) === 3, `${await customInputs.count()} 个`)

// 自定义回答能让重跑按钮可用，并计入「待生效」。
const rerun3 = p3.getByRole('button', { name: /按我的回答重跑/ })
await customInputs.first().fill('按 2025 年报口径')
await page.waitForTimeout(200)
check('自定义回答后按钮可用', await rerun3.isEnabled())
check('自定义回答计入待生效', (await p3.innerText()).includes('有 1 条待生效'))

// ⑤ 状态行必须能被读屏播报（重跑是异步的，没有可播报的变化等于没有反馈）。
const status = p3.locator('[role="status"][aria-live="polite"]')
check('状态行可被读屏播报', (await status.count()) >= 1)

// ① 改掉文本框再重跑：请求体里必须是**本轮那句命题**，不是文本框里的新内容。
await page.fill('textarea', '我认为中国平安估值已经回落但基本面并没有恶化')
await page.waitForTimeout(200)
const staleText = await p3.innerText()
check('文本框被改动后有提示', staleText.includes('本次重跑验证的仍是本轮那句话'))

posted.length = 0
await rerun3.click()
await page.waitForFunction(() => !document.body.innerText.includes('正在重跑'), { timeout: 180000 })
await page.waitForTimeout(800)
const sent = posted.map((b) => {
  try {
    return JSON.parse(b)
  } catch {
    return {}
  }
})
check(
  '重跑发的是本轮命题，不是文本框里的新内容',
  sent.length > 0 && sent.every((s) => s.raw_text === PROPOSITION),
  sent.map((s) => s.raw_text).join(' | ').slice(0, 90),
)
// 自定义回答确实发到了后端，并且被如实标注为「未改动任何子问题」——
// 后端支持自定义口径（有 Python 测试守着），界面上也得真把它送出去。
check(
  '自定义回答原样发出',
  sent.length > 0 && JSON.stringify(sent[sent.length - 1].clarifications || {}).includes('按 2025 年报口径'),
  JSON.stringify(sent[sent.length - 1]?.clarifications || {}).slice(0, 90),
)

// ④ 重跑途中选项按钮禁用：那一下点击会被返回后的回填静默丢掉。
await page.route('**/api/verify', async (route) => {
  await new Promise((r) => setTimeout(r, 1500))
  await route.continue()
})
await p3.locator('input[placeholder="或自己写一句…"]').first().fill('换个口径再看')
await page.waitForTimeout(200)
await rerun3.click()
await page.waitForTimeout(400)
check('重跑途中选项按钮禁用', await p3.locator('button[aria-pressed]').first().isDisabled())
await page.unroute('**/api/verify').catch(() => {})
await page.waitForFunction(() => !document.body.innerText.includes('正在重跑'), { timeout: 180000 })
await page.waitForTimeout(800)

check('第三段无控制台错误', errors.length === 0, errors.slice(0, 2).join(' | '))

// ---------------------------------------------------------------------------
// 第四段：标签必须说**实际执行了什么**，而不是一律说「计算」
//
// 28 个预设选项里只有 1 个会改子问题、13 个只是追加一条适用边界、8 个什么都不改。
// 面板此前对所有这些一律写「本轮按你的回答**计算**：」——同一个选项的效果被
// 并排两行写成互相打脸的两句话：标签说它改了计算，下面「它改变了什么」那行
// 说的却是「追加适用边界」。选「不确定」，判定与取数一个字都没动。
//
// 取 effect_kind 分派标签后，这一段盯的就是它没有退回成一句通用废话。
// ---------------------------------------------------------------------------

await page.unroute('**/api/verify').catch(() => {})
await page.goto(BASE + '/', { waitUntil: 'networkidle' })
await page.fill('textarea', PROPOSITION)
await page.getByRole('button', { name: /验证/ }).first().click()
await page.waitForSelector('#conclusion', { timeout: 180000 })
await page.locator('a[href="#parse"]').click()
await page.waitForTimeout(500)

const p4 = page.locator('#parse')
const verdictBefore = (await page.locator('#conclusion').innerText()).slice(0, 40)

// 背离型第 3 问的「不确定」：effect_kind = limitation，target 为空。
await p4.getByRole('button', { name: '不确定' }).click()
await page.waitForTimeout(200)
await p4.getByRole('button', { name: /按我的回答重跑/ }).click()
await page.waitForFunction(() => !document.body.innerText.includes('正在重跑'), { timeout: 180000 })
await page.waitForTimeout(800)

const t4 = await p4.innerText()
check('限缩型回答的标签说的是追加边界', t4.includes('追加了一条适用边界（判定与取数未变）：不确定'), t4.slice(0, 0))
check('限缩型回答不再被说成「计算」', !t4.includes('本轮按你的回答计算'))
check('限缩型回答出现在执行记录里', t4.includes('追加适用边界：不确定'))
// 判定真的没动 —— 标签说的是实话，不是换了个好听的说法。
const verdictAfter = (await page.locator('#conclusion').innerText()).slice(0, 40)
check('限缩型回答确实没有改变判定', verdictBefore === verdictAfter, `${verdictBefore} → ${verdictAfter}`)
check('第四段无控制台错误', errors.length === 0, errors.slice(0, 2).join(' | '))

// ---------------------------------------------------------------------------
// 第五段：对不上题面的回答必须**说出来**，不能静默丢弃
//
// 回答以题面为键，而题面由命题类型生成：命题一改、或类型判定换了一类，整组题面
// 就换掉，上一轮的回答会一条也对不上。此后端静默丢弃（errors 里没有痕迹），
// 按钮旁却写着「有 N 条待生效」—— 用户答了、产品没听见，却告诉他听见了。
//
// 这一段走的是真实渲染：拿一份真实返回塞进 unmatched_answers，页面照常加载。
// 界面上正常操作走不到这个分支（回答与题面同源），它守的是 API 与
// 「配了 key 时 LLM 的类型判定在两次运行间翻转」那条路。
// ---------------------------------------------------------------------------

const stale = await page.evaluate(async (text) => {
  const r = await fetch('/api/verify', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ raw_text: text, clarifications: {} }),
  })
  return r.json()
}, PROPOSITION)
stale.parsed.unmatched_answers = ['与同业比', '按 2025 年报口径']

await page.unroute('**/api/verify').catch(() => {})
await page.route('**/api/verify', (route) =>
  route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(stale) }),
)

await page.goto(BASE + '/', { waitUntil: 'networkidle' })
await page.fill('textarea', PROPOSITION)
await page.getByRole('button', { name: /验证/ }).first().click()
await page.waitForSelector('#conclusion', { timeout: 180000 })
await page.locator('a[href="#parse"]').click()
await page.waitForTimeout(500)

const t5 = await page.locator('#parse').innerText()
check('对不上的回答被报出来', t5.includes('2 条回答对不上本轮的任何一道澄清问题'))
check('报出的是哪几条', t5.includes('与同业比') && t5.includes('按 2025 年报口径'))
check('说明本轮没有采用它们', t5.includes('本轮没有采用这些回答'))
check('说明了为什么会这样', t5.includes('澄清问题是按命题类型生成的'))
check(
  '这一条是 alert，读屏也能听到',
  (await page.locator('#parse [role="alert"]').count()) >= 1,
)
check('第五段无控制台错误', errors.length === 0, errors.slice(0, 2).join(' | '))

// 全页 DOM 不得有未渲染的 **（第二、五段注入的返回也要过一遍）
const stars2 = await page.evaluate(() => {
  const out = []
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
  for (let n = w.nextNode(); n; n = w.nextNode()) if (n.textContent.includes('**')) out.push(n.textContent.trim().slice(0, 60))
  return out
})
check('注入分支也无 ** 泄漏', stars2.length === 0, stars2.join(' | '))

await browser.close()
console.log(failed ? `\n澄清环节验收：${failed} 项失败` : '\n澄清环节验收：全部通过')
process.exit(failed ? 1 : 0)
