# laya-edge — 젯슨 Xavier NX(arm64)에서 직접 빌드한다:  cd /data/edge && docker compose build
# onnxruntime-node 는 linux arm64 CPU 판을 함께 받는다(GPU 실행은 이 이미지 범위 밖 — docs/01 §3-1 B 안).
FROM node:20-bookworm-slim

WORKDIR /app
COPY package.json package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci --omit=dev; else npm install --omit=dev; fi \
 && npm cache clean --force
COPY server.mjs bench.mjs sample_request.json ./

ENV NODE_ENV=production PORT=8000 HOST=0.0.0.0 LAYA_MODEL_DIR=/models/current LAYA_THREADS=3
USER node
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:8000/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
CMD ["node", "server.mjs"]
