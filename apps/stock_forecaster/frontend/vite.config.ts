import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig(({ mode }) => ({
  plugins: [react()],
  define: mode === 'research-preview' ? {
    'import.meta.env.VITE_API_BASE_URL': JSON.stringify(''),
    'import.meta.env.VITE_EVIDENCE_API_BASE_URL': JSON.stringify(''),
  } : undefined,
  server: mode === 'research-preview' ? {
    proxy: {
      '/api/v2/evidence': { target: 'http://127.0.0.1:8013', changeOrigin: true },
      '/api': { target: 'http://127.0.0.1:8012', changeOrigin: true },
    },
  } : undefined,
  test: {
    include: ['src/test/**/*.test.{ts,tsx}'],
    environment: 'jsdom',
    setupFiles: './src/test/setup.tsx',
  },
}))
