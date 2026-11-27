// 가짜 api(8001)와 vite 개발 서버(3000)를 한 번에 띄운다 — 실제 장치 없이 화면을 보고 캡처할 때.
//   node 개발용도구/개발서버실행.mjs
// vite 는 SMARTCUBE_API=http://127.0.0.1:8001 로 /api, /i/<iid>/api 를 가짜 api 에 넘긴다(vite.config.ts 의 proxy).
// 열리는 주소: http://localhost:3000/  (가짜 api 의 /me 가 인스턴스 하나를 주므로 /i/jetson-01/ 로 바로 이동한다)
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const fr = path.resolve(here, '..');
const apiPort = process.env.MOCK_PORT ?? '8001';
const webPort = process.env.PORT ?? '3000';
// --lan: 가짜 api 를 0.0.0.0 에 열어 같은 공유기의 설정 보드(ESP32)가 붙을 수 있게 한다(설정 보드 로컬 시험, 2026-11-16).
//        Windows 방화벽에서 8001 인바운드를 허용해야 한다.
if (process.argv.includes('--lan')) process.env.MOCK_HOST = '0.0.0.0';

await import('./가짜api.mjs');

const vite = spawn(
  process.execPath,
  [path.join(fr, 'node_modules', 'vite', 'bin', 'vite.js'), fr, '--port', webPort, '--host', '0.0.0.0', '--strictPort'],
  { cwd: fr, env: { ...process.env, SMARTCUBE_API: process.env.SMARTCUBE_API ?? `http://127.0.0.1:${apiPort}` }, stdio: 'inherit' },
);
vite.on('exit', (code) => process.exit(code ?? 0));
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => { vite.kill(); process.exit(0); });
