/**
 * 录制演示视频。在真实无头 Chromium 里按脚本走一遍产品，同时录屏。
 *
 * 每个场景的停留时长 === 该段旁白的实际时长（从 narration.json 读，
 * 那份文件由 TTS 生成时量出）。这样音画对齐是算出来的，不靠手工掐秒。
 *
 * 用法（先起后端，或直接指向线上）：
 *   UI_BASE=http://39.96.194.197/thesis node scripts/demo-video.mjs
 * 产物：<VIDEO_DIR>/raw/*.webm，再由 ffmpeg 加旁白合成 mp4。
 *
 * 注意 VIDEO_DIR 的默认值：Windows 上 `/tmp` 不是 `C:\tmp`，而是
 * `C:\Users\<用户>\AppData\Local\Temp`。写死成 `C:/tmp/vid` 会让
 * mkdirSync 凭空造出一个空目录，再去读 narration.json 就找不到文件。
 */
import { chromium } from 'playwright'
import { mkdirSync, writeFileSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const BASE = process.env.UI_BASE || 'http://127.0.0.1:8000'
const OUT = process.env.VIDEO_DIR || join(tmpdir(), 'vid')
const NARRATION = `${OUT}/narration.json`

mkdirSync(`${OUT}/raw`, { recursive: true })
const durations = JSON.parse(readFileSync(NARRATION, 'utf8'))

// 题目要求 60–180 秒。这个区间是硬约束，不是建议，所以写在录之前而不是剪完再量：
// 旁白本身就是长度的下限（动作再慢也不会短于它），先拦一道，免得录完 3 分钟才发现超。
const planned = Object.values(durations).reduce(
  (a, v) => a + (v > 1000 ? v / 1000 : v),
  0,
)
if (planned < 60 || planned > 180) {
  console.error(
    `旁白合计 ${planned.toFixed(1)}s，超出 60–180s 区间。先改 scenes.json 重新合成语音，别录。`,
  )
  process.exit(1)
}
console.log(`旁白合计 ${planned.toFixed(1)}s，在 60–180s 内，开始录制。\n`)

const THESIS = {
  maotai: '我认为贵州茅台的估值已经回落，但基本面并没有恶化',
  squeeze: '我认为碳酸锂价格上涨会挤压宁德时代的利润空间',
}

const browser = await chromium.launch()
const page = await browser.newPage({
  viewport: { width: 1280, height: 720 },
  recordVideo: { dir: `${OUT}/raw`, size: { width: 1280, height: 720 } },
})

/** 平滑滚动，避免录出来是一帧跳变。 */
async function glide(selector, ms = 900) {
  await page.evaluate(
    ([sel, dur]) =>
      new Promise((res) => {
        const el = document.querySelector(sel)
        if (!el) return res()
        const from = window.scrollY
        const to = el.getBoundingClientRect().top + window.scrollY - 60
        const t0 = performance.now()
        const tick = (t) => {
          const k = Math.min(1, (t - t0) / dur)
          const e = k < 0.5 ? 2 * k * k : 1 - (-2 * k + 2) ** 2 / 2
          window.scrollTo(0, from + (to - from) * e)
          if (k < 1) requestAnimationFrame(tick)
          else res()
        }
        requestAnimationFrame(tick)
      }),
    [selector, ms],
  )
}

async function verify(text) {
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.fill('textarea', text)
  await page.getByRole('button', { name: /验证/ }).first().click()
  await page.waitForSelector('#conclusion', { timeout: 120000 })
  await page.waitForTimeout(800)
}

// 每个场景：先做到位，再按该段旁白的时长停留
const scenes = {
  async s1() {
    await page.goto(BASE + '/', { waitUntil: 'networkidle' })
    await page.waitForTimeout(600)
  },
  async s2() {
    await verify(THESIS.maotai)
    await glide('#decompose', 1200)
  },
  async s3() {
    await glide('#conflict', 1100)
  },
  async s4() {
    await glide('#falsify', 1100)
    // 展开第一行的「阈值依据」
    const row = page.locator('#falsify tbody tr').first()
    await row.click()
    await page.waitForTimeout(500)
  },
  async s5() {
    await verify(THESIS.squeeze)
    await glide('#evidence', 1300)
  },
  async s6() {
    await glide('#research', 1100)
    await page.getByRole('button', { name: '开始比较' }).click()
    await page.waitForSelector('#research table tbody tr', { timeout: 180000 })
    await page.waitForTimeout(400)
  },
  async s7() {
    await page.getByRole('button', { name: '追问' }).click()
    await page.getByRole('button', { name: '展开这一条' }).click()
    await page.waitForSelector('#research .whitespace-pre-wrap', { timeout: 60000 })
    // 旁白这半句是在讲「追问」，得让它真的在屏幕上待够时间。
    // 早先这里只停 0.5s 就切走了，观众还没看清追问答了什么，画面已经换成研究任务。
    await page.waitForTimeout(4200)
    await page.getByRole('button', { name: '研究任务' }).click()
    await page.getByRole('button', { name: /存为研究任务/ }).click()
    await page.waitForTimeout(1500)
  },
  async s8() {
    await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'smooth' }))
    await page.waitForTimeout(900)
  },
}

// 记下每段画面在视频里的**真实起始秒**。停留是用 setTimeout 补的，
// 每次都会多出一点点调度开销；八段累起来就有一秒多，旁白会整体提前于画面。
// 与其事后目测，不如把真实边界写下来，合成时按它塞静音。
const timing = {}
const t0All = Date.now()
for (const [sid, value] of Object.entries(durations)) {
  // narration.json 里的值是**秒**（TTS 量出来的），不是毫秒。
  // 早先这里写成 value / 1000，于是每段停留都成了 0，录出来只有动作没有停留。
  // 保留 >1000 当毫秒的解释，是为了这份文件换单位时不至于静默录错。
  const seconds = value > 1000 ? value / 1000 : value
  timing[sid] = { start: (Date.now() - t0All) / 1000, narration: seconds }
  process.stdout.write(`${sid} (${seconds.toFixed(1)}s) ... `)
  const t0 = Date.now()
  await scenes[sid]()
  const spent = (Date.now() - t0) / 1000
  // 补齐到旁白长度；若动作本身已经超时，就不再等（宁可画长于音）
  const rest = Math.max(0, seconds - spent)
  await page.waitForTimeout(rest * 1000)
  timing[sid].end = (Date.now() - t0All) / 1000
  console.log(
    `动作 ${spent.toFixed(1)}s + 停留 ${rest.toFixed(1)}s → 画面 ${(timing[sid].end - timing[sid].start).toFixed(2)}s`,
  )
}
writeFileSync(`${OUT}/timing.json`, JSON.stringify(timing, null, 1))

await page.close()
const videoPath = await page.video().path()
writeFileSync(`${OUT}/raw-path.txt`, videoPath)
console.log('raw video:', videoPath)
await browser.close()
