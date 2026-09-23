/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        // 三态语义色。整个界面只允许用这三组颜色表达「证据指向哪一边」，
        // 不允许出现第四种表示方向的颜色——颜色一旦多义，读者就无法秒读。
        sup: { bg: '#f0fdf4', line: '#86efac', fg: '#166534', dot: '#16a34a' },
        ref: { bg: '#fff1f2', line: '#fda4af', fg: '#9f1239', dot: '#e11d48' },
        unv: { bg: '#f8fafc', line: '#cbd5e1', fg: '#475569', dot: '#94a3b8' },
        ink: { 900: '#0f172a', 700: '#334155', 500: '#64748b', 300: '#cbd5e1', 100: '#f1f5f9' },
      },
      fontFamily: {
        sans: ['-apple-system', 'BlinkMacSystemFont', '"Segoe UI"', '"Microsoft YaHei"',
               '"PingFang SC"', '"Hiragino Sans GB"', 'sans-serif'],
        mono: ['"SF Mono"', 'Consolas', '"Liberation Mono"', 'monospace'],
      },
    },
  },
  plugins: [],
}
