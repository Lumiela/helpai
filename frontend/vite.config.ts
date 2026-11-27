import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import {defineConfig} from 'vite';

export default defineConfig(() => {
  return {
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, '.'),
      },
    },
    server: {
      // HMR is disabled in AI Studio via DISABLE_HMR env var.
      // Do not modify—file watching is disabled to prevent flickering during agent edits.
      hmr: process.env.DISABLE_HMR !== 'true',
      // Disable file watching when DISABLE_HMR is true to save CPU during agent edits.
      watch: process.env.DISABLE_HMR === 'true' ? null : {},
      // 개발 서버에서 /api/… 를 젯슨 api 서비스로 넘긴다. 젯슨의 nginx 와 같은 규칙(접두어 /api 를 뗀다).
      // 기본은 SSH 터널(ssh -N -L 127.0.0.1:8000:172.18.0.8:8000 jetson). 다른 주소면 SMARTCUBE_API 환경변수.
      // 2026-09-29 오후: 인스턴스 라우트 /i/<iid>/api/… → /i/<iid>/… (젯슨 nginx 와 같은 규칙)
      proxy: {
        '^/i/[a-z0-9][a-z0-9_-]*/api/': {
          target: process.env.SMARTCUBE_API ?? 'http://127.0.0.1:8000',
          changeOrigin: true,
          rewrite: (requestPath: string) => requestPath.replace(/^(\/i\/[^/]+)\/api\//, '$1/'),
        },
        '/api': {
          target: process.env.SMARTCUBE_API ?? 'http://127.0.0.1:8000',
          changeOrigin: true,
          rewrite: (requestPath: string) => requestPath.replace(/^\/api/, ''),
        },
      },
    },
  };
});
