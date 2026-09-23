/**
 * 构建前清空 dist，并且**删不干净就报错**。
 *
 * 起因是一次真实的坑：uvicorn 正挂着 `frontend/dist` 时跑 `vite build`，
 * 带 hash 的旧 bundle 删不掉，vite 的 emptyOutDir 不会因此失败——
 * 它只是留着那些文件继续生成新的。于是 dist/assets 里堆了 10 个 bundle，
 * `git add -A` 把它们全带进了提交，而 index.html 只引用其中一个。
 *
 * 这类失败的危险恰恰在于它是静默的：构建输出看起来完全正常。
 * 所以这里手动删一遍，然后**检查目录是否真的空了**——
 * 没删干净就带着「哪个文件被占用」的信息退出，而不是继续往下走。
 */
import { existsSync, readdirSync, rmdirSync, statSync, unlinkSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const dist = join(root, 'dist')
if (!existsSync(dist)) process.exit(0)

/**
 * 手动自底向上删。这里刻意不用 `fs.rmSync(dir, {recursive:true})`——
 * 在本仓库的路径下（路径含中文）它**不报错也不删文件**，返回得很干净，
 * 目录原封不动。实测 `unlinkSync` / `rmdirSync` 在同一条路径上工作正常，
 * 所以这层递归由我们自己走。
 *
 * 被占用的文件（还在跑的 uvicorn 会占住 dist 里的 bundle）会在 unlink 时
 * 抛 EBUSY/EPERM，正好用来报出「谁还在用它」。
 */
const failed = []
function purge(dir) {
  let names
  try {
    names = readdirSync(dir)
  } catch (e) {
    failed.push([dir, e.code || e.message])
    return
  }
  for (const name of names) {
    const p = join(dir, name)
    let st
    try {
      st = statSync(p)
    } catch (e) {
      failed.push([p, e.code || e.message])
      continue
    }
    if (st.isDirectory()) purge(p)
    else {
      try {
        unlinkSync(p)
      } catch (e) {
        failed.push([p, e.code || e.message])
      }
    }
  }
  try {
    rmdirSync(dir)
  } catch (e) {
    if (e.code !== 'ENOTEMPTY') failed.push([dir, e.code || e.message])
  }
}

purge(dist)

if (existsSync(dist)) {
  console.error(`\n[clean-dist] dist 没有被删干净，${failed.length} 个文件删不掉：`)
  for (const [p, why] of failed.slice(0, 20)) {
    console.error(`  ${p.slice(root.length + 1)}  (${why})`)
  }
  console.error('[clean-dist] 多半是 uvicorn 还在跑（它会挂载 dist 里的静态文件）。')
  console.error('[clean-dist] 用 netstat -ano | grep ":8000" 找到 PID，只结束那一个进程后重试。')
  console.error('[clean-dist] 继续构建会把这些旧 bundle 一起带进提交。已中止。\n')
  process.exit(1)
}
