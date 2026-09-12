import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    rollupOptions: {
      output: {
        // Keep heavy optional libs in stable vendor chunks so route opens
        // don't re-download/parse them mixed into page modules.
        manualChunks(id) {
          if (!id.includes('node_modules')) return
          if (id.includes('@xterm')) return 'vendor-xterm'
          if (id.includes('@xyflow') || id.includes('dagre')) return 'vendor-topology'
          if (id.includes('uplot')) return 'vendor-uplot'
          if (id.includes('@tanstack')) return 'vendor-query'
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/v1": {
        target: "http://127.0.0.1:8890",
        changeOrigin: true,
        ws: true,
        // Keep Origin aligned with upstream for WS handshake through proxy.
        rewriteWsOrigin: true,
        // So NetX sees the browser address instead of 127.0.0.1 (X-Forwarded-For).
        xfwd: true,
      },
      "/metrics": {
        target: "http://127.0.0.1:8890",
        changeOrigin: true,
      },
    },
  },
})
