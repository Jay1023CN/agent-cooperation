import { defineConfig } from 'vite';

export default defineConfig({
  base: '/static/',
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8765', changeOrigin: false },
      '/health': { target: 'http://127.0.0.1:8765', changeOrigin: false },
    },
  },
  build: {
    outDir: '../arena/static',
    emptyOutDir: true,
    sourcemap: false,
  },
});
