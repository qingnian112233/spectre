import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// base:'./' → 产物用相对路径, nginx 直接挂在根目录也能跑(/assets/...)
// __BUILD__: 编译时刻(北京时间 MM-DD HH:mm), 打到页面右上角。
//   2026-09-20 老板连着两次「没有变啊」—— 光看界面分不清是"改没生效"还是
//   "Telegram WebView 吃了旧包"。有这个标记, 他自己一眼就能对账。
export default defineConfig({
  base: './',
  plugins: [react(), tailwindcss()],
  define: {
    __BUILD__: JSON.stringify(
      new Date().toLocaleString('sv-SE', { timeZone: 'Asia/Shanghai' }).slice(5, 16),
    ),
  },
  build: { outDir: 'dist', emptyOutDir: true, chunkSizeWarningLimit: 1500 },
})
