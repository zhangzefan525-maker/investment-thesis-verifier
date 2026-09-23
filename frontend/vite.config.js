import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 构建产物直接落到 frontend/dist，后端 main.py 会把它挂到 / 上。
// 开发时用 proxy 把 /api 转给本地后端，避免前后端两套地址。
//
// base 走环境变量：线上这台机器 80 端口已经给了另一个项目，本产品只能挂在
// 子路径 `/thesis/` 下（nginx 用 location /thesis/ 反代并在转发前剥掉前缀，
// 所以后端看不到这段前缀，也不需要任何改动）。本地构建仍是 `/`，
// 免得开发时多出一层路径。
const base = process.env.VITE_BASE || '/'

export default defineConfig({
  base,
  plugins: [react()],
  server: {
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
  build: { outDir: 'dist', emptyOutDir: true },
})
