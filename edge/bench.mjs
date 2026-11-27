// 지연 실측 — node bench.mjs [--url http://127.0.0.1:8000] [--n 20] [--file sample_request.json]
// LAYA_API_KEY 환경변수가 있으면 Bearer 로 보낸다. 첫 요청(예열)은 통계에서 뺀다.
import { readFile } from "node:fs/promises";

const arg = (name, def) => {
  const i = process.argv.indexOf("--" + name);
  return i > 0 ? process.argv[i + 1] : def;
};
const url = arg("url", "http://127.0.0.1:8000").replace(/\/$/, "") + "/v1/systemone";
const n = Number(arg("n", 20));
const body = await readFile(arg("file", new URL("./sample_request.json", import.meta.url)), "utf8");
const headers = { "Content-Type": "application/json" };
if (process.env.LAYA_API_KEY) headers.Authorization = "Bearer " + process.env.LAYA_API_KEY;

const times = [];
let last;
for (let i = 0; i <= n; i++) {
  const t0 = performance.now();
  const r = await fetch(url, { method: "POST", headers, body });
  last = await r.json();
  if (!r.ok) { console.error("실패", r.status, last); process.exit(1); }
  if (i > 0) times.push(performance.now() - t0);
}
times.sort((a, b) => a - b);
const p = (q) => Math.round(times[Math.min(times.length - 1, Math.floor(times.length * q))]);
console.log(`요청 ${times.length} 회 · 질문 ${Object.keys(JSON.parse(body).questions).length} 개 · p50 ${p(0.5)} ms · p95 ${p(0.95)} ms · 최대 ${Math.round(times.at(-1))} ms`);
console.log("마지막 답:", JSON.stringify(last.answers, null, 1).slice(0, 1500));
