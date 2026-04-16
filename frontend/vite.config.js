import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/chat': 'http://localhost:8080',
      '/new-chat': 'http://localhost:8080',
      '/feedback': 'http://localhost:8080',
      '/documents': 'http://localhost:8080',
      '/healthz': 'http://localhost:8080',
      '/agents': 'http://localhost:8080',
    }
  }
})
