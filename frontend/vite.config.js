import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 构建产物直接落到 frontend/dist，后端 main.py 会把它挂到 / 上。
// 开发时用 proxy 把 /api 转给本地后端，避免前后端两套地址。
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
  build: { outDir: 'dist', emptyOutDir: true },
})
