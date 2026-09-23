/**
 * 端到端 UI 冒烟检查（真实 headless Chromium，不是 jsdom）。
 *
 * 后端测试跑在 Python 层，看不见「页面渲染成什么样」。这个脚本补的就是那一段：
 * 真实浏览器里点一遍四条例题，断言四件事——
 *   1. 四例的结论与预期一致（支持 / 不支持 / 证据不足 / 不支持）；
 *   2. 全程零 console error、零 pageerror；
 *   3. 页面上不出现未渲染的 `**` 标记（RichText 漏接会留下星号）；
 *   4. 「继续用」三个 tab 都能真的跑出结果，不是摆着好看。
 *
 * 用法（需先起后端，后端会挂载 frontend/dist）：
 *   cd backend && uvicorn app.main:app --port 8000
 *   cd frontend && node scripts/ui-smoke.mjs
 * 退出码非 0 代表有断言失败。
 */
import { chromium } from 'playwright'

const BASE = process.env.UI_BASE || 'http://127.0.0.1:8000'

// 四条例题各命中一种结论。期望值来自实跑，不是从文档抄的。
// 这里比对的是结论徽章的**完整文案**：只判断「包含『支持』」是不够的，
// 「现有证据不支持」同样包含这两个字——那样这条断言就永远为真，等于没测。
const CASES = [
  {
    name: '贵州茅台 · 背离型',
    text: '我认为贵州茅台的估值已经回落，但基本面并没有恶化',
    chip: '现有证据不支持',
  },
  {
    name: '中国平安 · 背离型',
    text: '我认为中国平安的估值已经回落，但基本面并没有恶化',
    chip: '现有证据支持',
  },
  {
    name: '宁德时代 · 归因型',
    text: '我认为宁德时代的盈利改善来自主营业务',
    chip: '现有证据不支持',
  },
  {
    name: '宁德时代 · 传导型',
    text: '我认为碳酸锂价格上涨会挤压宁德时代的利润空间',
    chip: '证据不足以判断',
  },
]

const failures = []
const check = (ok, msg) => {
  if (ok) return
  failures.push(msg)
  console.log('  ✗ ' + msg)
}

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } })

const errors = []
page.on('console', (m) => {
  if (m.type() === 'error') errors.push(m.text())
})
page.on('pageerror', (e) => errors.push('pageerror: ' + e.message))

async function runThesis(text) {
  await page.goto(BASE + '/', { waitUntil: 'networkidle' })
  await page.fill('textarea', text)
  await page.getByRole('button', { name: /验证/ }).first().click()
  await page.waitForSelector('#conclusion', { timeout: 120000 })
  // 等图表与冲突区渲染完，否则 DOM 扫描扫不到它们
  await page.waitForTimeout(900)
}

// --------------------------------------------------------------------------
// 一、四例主链路 × 渲染正确性
// --------------------------------------------------------------------------

console.log('\n[1] 四例主链路')
for (const c of CASES) {
  console.log(`- ${c.name}`)
  await runThesis(c.text)

  const chip = (await page.locator('#conclusion .chip').first().innerText()).trim()
  check(chip === c.chip, `结论应为「${c.chip}」，实际「${chip}」`)

  // 关键区块都必须存在
  for (const sel of ['#decompose', '#evidence']) {
    check((await page.locator(sel).count()) > 0, `缺少区块 ${sel}`)
  }

  // `**` 泄漏：RichText 漏接的地方会把星号原样渲染出来
  const leaked = await page.evaluate(() => {
    const bad = []
    const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
    let n
    while ((n = walk.nextNode())) {
      const t = n.nodeValue || ''
      if (t.includes('**')) bad.push(t.trim().slice(0, 80))
    }
    return bad
  })
  check(leaked.length === 0, `页面存在未渲染的 ** 标记：${JSON.stringify(leaked)}`)
}

// --------------------------------------------------------------------------
// 二、控制台干净度（四例跑完后统一判，避免重复噪声）
// --------------------------------------------------------------------------

console.log('\n[2] 控制台')
check(errors.length === 0, `出现 ${errors.length} 条 console/page error：${JSON.stringify(errors.slice(0, 3))}`)
if (errors.length === 0) console.log('  ✓ 全程零 error')

// --------------------------------------------------------------------------
// 三、「继续用」三个 tab —— 比较 / 追问 / 存为研究任务
// --------------------------------------------------------------------------

console.log('\n[3] 继续用')
await runThesis('我认为贵州茅台的估值已经回落，但基本面并没有恶化')

// 3.1 横向比较
await page.getByRole('button', { name: '开始比较' }).click()
await page.waitForSelector('#research table tbody tr', { timeout: 180000 })
const rows = await page.locator('#research table tbody tr').count()
check(rows >= 2, `横向比较应至少 2 行，实际 ${rows}`)
if (rows >= 2) console.log(`  ✓ 横向比较：${rows} 行`)

// 3.2 追问
await page.getByRole('button', { name: '追问' }).click()
await page.getByRole('button', { name: '展开这一条' }).click()
await page.waitForSelector('#research .whitespace-pre-wrap', { timeout: 60000 })
const answer = (await page.locator('#research .whitespace-pre-wrap').first().innerText()).trim()
check(answer.length > 0, '追问答案为空')
check(!answer.includes('**'), '追问答案里有未渲染的 ** 标记')
if (answer && !answer.includes('**')) console.log(`  ✓ 追问：答案 ${answer.length} 字，无 ** 泄漏`)

// 3.3 存为研究任务
await page.getByRole('button', { name: '研究任务' }).click()
await page.getByRole('button', { name: /存为研究任务/ }).click()
await page.waitForTimeout(2000)
const tasks = await page.locator('#research ul li').count()
check(tasks > 0, '保存研究任务后没有出现反转条件条目')
if (tasks > 0) console.log(`  ✓ 研究任务：${tasks} 条反转条件`)

await browser.close()

// --------------------------------------------------------------------------

console.log('')
if (failures.length) {
  console.log(`UI 冒烟：${failures.length} 项失败`)
  process.exit(1)
}
console.log('UI 冒烟：全部通过')
