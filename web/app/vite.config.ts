import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Build output goes into the python package so `applypilot app` serves it.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    outDir: '../../src/applypilot/webdist',
    emptyOutDir: true,
  },
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8765',
    },
  },
})
