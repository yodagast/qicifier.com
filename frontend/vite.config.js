import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

const apiTarget = process.env.VITE_API_TARGET || 'http://127.0.0.1:5172'

// 注意：**不要**给 /research 加代理。行业研究是站点自己的路由（正文由 /api/research/<slug>
// 取回后嵌进页面），加代理会让 dev 打到后端、线上走 SPA，两边行为不一致；
// 数据仍然走 /api，下面那条代理已经覆盖。

export default defineConfig({
  plugins: [vue()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': { target: apiTarget, changeOrigin: true },
    },
  },
  preview: {
    host: '127.0.0.1',
    port: 4173,
    proxy: {
      '/api': { target: apiTarget, changeOrigin: true },
    },
  },
})