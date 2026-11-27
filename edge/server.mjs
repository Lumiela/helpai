// 헬피아이 laya-edge — 젯슨(또는 집 PC)에서 Laya ONNX 묶음으로 추론만 하는 작은 HTTP 서버.
//
//   POST /v1/systemone   {state, questions} → {model, revision, answers, latency_ms}   (Laya·Jev 와 같은 모양)
//   GET  /health         적재 여부·실행 제공자·지연 p50/p95·메모리         (인증 없음, 내부망 전용)
//   GET  /model          묶음 manifest.json
//   POST /admin/reload   LAYA_MODEL_DIR(보통 current 링크)를 다시 적재. 실패하면 이전 모델 유지
//
// 환경변수
//   LAYA_MODEL_DIR       묶음 폴더 (기본 /models/current)
//   LAYA_API_KEY         있으면 /v1/systemone·/model·/admin/* 에 Bearer 필요
//   LAYA_THREADS         ONNX Runtime 스레드 수 (기본 3 — 젯슨 6코어의 절반)
//   LAYA_PROVIDERS       실행 제공자 목록 (기본 cpu)
//   LAYA_ALLOW_UNBUNDLED 1 이면 SHA256SUMS·manifest 없는 폴더(ONNX 내보내기 결과 그대로)도 적재 — 집 PC 비교 시험용
//   HOST / PORT          기본 0.0.0.0 / 8000
//
// 요청은 한 번에 하나씩 처리한다(메모리 급증 방지). 장치에서 온 문자열은 rules 가 넣지 않으므로 여기서는 크기만 제한한다.
import http from "node:http";
import { createHash, timingSafeEqual } from "node:crypto";
import { createReadStream, existsSync } from "node:fs";
import { readFile, realpath } from "node:fs/promises";
import path from "node:path";
import { Laya } from "@receptron/laya";

const PORT = Number(process.env.PORT || 8000);
const HOST = process.env.HOST || "0.0.0.0";
const MODEL_DIR = process.env.LAYA_MODEL_DIR || "/models/current";
const API_KEY = process.env.LAYA_API_KEY || "";
const THREADS = Number(process.env.LAYA_THREADS || 3);
const PROVIDERS = (process.env.LAYA_PROVIDERS || "cpu").split(",").map((s) => s.trim()).filter(Boolean);
const ALLOW_UNBUNDLED = process.env.LAYA_ALLOW_UNBUNDLED === "1";
const MAX_BODY = 256 * 1024;
const MAX_QUESTIONS = 16;

let current = null;          // {laya, manifest, dir, loadedAt}
let lastError = null;
const latencies = [];        // 최근 200 건(ms)

const log = (...a) => console.log(new Date().toISOString(), ...a);

function sha256(file) {
  return new Promise((resolve, reject) => {
    const h = createHash("sha256");
    createReadStream(file).on("data", (b) => h.update(b)).on("end", () => resolve(h.digest("hex"))).on("error", reject);
  });
}

async function verifyBundle(dir) {
  const sums = path.join(dir, "SHA256SUMS");
  if (!existsSync(sums)) return ALLOW_UNBUNDLED ? [] : ["SHA256SUMS 없음"];
  const problems = [];
  for (const raw of (await readFile(sums, "utf8")).split("\n")) {
    const line = raw.replace(/\r$/, "");
    if (!line.trim()) continue;
    const i = line.indexOf("  ");
    const digest = line.slice(0, i);
    const rel = line.slice(i + 2);
    const file = path.join(dir, rel);
    if (!path.resolve(file).startsWith(path.resolve(dir) + path.sep)) { problems.push("경로 이탈: " + rel); continue; }
    if (!existsSync(file)) { problems.push("파일 없음: " + rel); continue; }
    if ((await sha256(file)) !== digest) problems.push("해시 불일치: " + rel);
  }
  return problems;
}

async function load() {
  const dir = await realpath(MODEL_DIR);
  const t0 = Date.now();
  const problems = await verifyBundle(dir);
  if (problems.length) throw new Error("묶음 검사 실패: " + problems.slice(0, 5).join("; "));
  const mfile = path.join(dir, "manifest.json");
  const manifest = existsSync(mfile) ? JSON.parse(await readFile(mfile, "utf8")) : { revision: "unbundled:" + path.basename(dir) };
  const laya = await Laya.load({
    modelDir: dir,
    executionProviders: PROVIDERS,
    sessionOptions: { intraOpNumThreads: THREADS, interOpNumThreads: 1 },
  });
  log("적재", manifest.revision, "from", dir, "(" + (Date.now() - t0) + " ms)", "providers", PROVIDERS.join(","));
  return { laya, manifest, dir, loadedAt: new Date().toISOString() };
}

// 한 번에 하나씩
let chain = Promise.resolve();
function exclusive(fn) {
  const p = chain.then(fn, fn);
  chain = p.catch(() => {});
  return p;
}

function send(res, code, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(code, { "Content-Type": "application/json; charset=utf-8", "Content-Length": Buffer.byteLength(body) });
  res.end(body);
}

function authorized(req) {
  if (!API_KEY) return true;
  const got = Buffer.from(String(req.headers.authorization || ""));
  const want = Buffer.from("Bearer " + API_KEY);
  return got.length === want.length && timingSafeEqual(got, want);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let n = 0;
    const chunks = [];
    req.on("data", (c) => {
      n += c.length;
      if (n > MAX_BODY) { reject(Object.assign(new Error("요청이 너무 크다"), { code: 413 })); req.destroy(); return; }
      chunks.push(c);
    });
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    req.on("error", reject);
  });
}

function pct(arr, p) {
  if (!arr.length) return null;
  const s = [...arr].sort((a, b) => a - b);
  return s[Math.min(s.length - 1, Math.floor(s.length * p))];
}

async function systemOne(req, res) {
  if (!current) return send(res, 503, { error: "model_not_loaded", detail: lastError });
  let body;
  try {
    body = JSON.parse(await readBody(req));
  } catch (e) {
    return send(res, e.code === 413 ? 413 : 400, { error: "bad_json", detail: String(e.message) });
  }
  const { state, questions } = body || {};
  if (state === undefined || state === null || typeof questions !== "object" || !questions) {
    return send(res, 422, { error: "state_and_questions_required" });
  }
  if (Object.keys(questions).length > MAX_QUESTIONS) return send(res, 422, { error: "too_many_questions" });
  try {
    const out = await exclusive(async () => {
      const t0 = performance.now();
      const r = await current.laya.systemOne(state, questions);
      const ms = performance.now() - t0;
      latencies.push(ms);
      if (latencies.length > 200) latencies.shift();
      return { ...r, revision: current.manifest.revision, latency_ms: Math.round(ms) };
    });
    return send(res, 200, out);
  } catch (e) {
    log("추론 실패", e);
    return send(res, 500, { error: "inference_failed", detail: String(e.message || e) });
  }
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, "http://x");
  try {
    if (req.method === "GET" && url.pathname === "/health") {
      const m = process.memoryUsage();
      return send(res, current ? 200 : 503, {
        ok: Boolean(current), revision: current?.manifest.revision ?? null, loaded_at: current?.loadedAt ?? null,
        providers: PROVIDERS, threads: THREADS, latency_ms: { p50: pct(latencies, 0.5), p95: pct(latencies, 0.95), n: latencies.length },
        rss_mb: Math.round(m.rss / 2 ** 20), last_error: lastError,
      });
    }
    if (!authorized(req)) return send(res, 401, { error: "unauthorized" });
    if (req.method === "POST" && url.pathname === "/v1/systemone") return systemOne(req, res);
    if (req.method === "GET" && url.pathname === "/model") {
      return current ? send(res, 200, current.manifest) : send(res, 503, { error: "model_not_loaded" });
    }
    if (req.method === "POST" && url.pathname === "/admin/reload") {
      try {
        const next = await exclusive(load);
        current = next;
        lastError = null;
        return send(res, 200, { ok: true, revision: next.manifest.revision });
      } catch (e) {
        lastError = String(e.message || e);
        log("다시 적재 실패 — 이전 모델 유지:", lastError);
        return send(res, 500, { ok: false, error: lastError, kept: current?.manifest.revision ?? null });
      }
    }
    return send(res, 404, { error: "not_found" });
  } catch (e) {
    log("요청 처리 오류", e);
    if (!res.headersSent) send(res, 500, { error: "internal" });
  }
});

server.listen(PORT, HOST, () => log("laya-edge 대기", HOST + ":" + PORT, "model dir", MODEL_DIR));
load().then((c) => { current = c; }).catch((e) => { lastError = String(e.message || e); log("적재 실패:", lastError); });

for (const sig of ["SIGTERM", "SIGINT"]) {
  process.on(sig, () => { log("종료", sig); server.close(() => process.exit(0)); setTimeout(() => process.exit(0), 3000).unref(); });
}
