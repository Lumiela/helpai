#!/usr/bin/env bash
# 집 PC(WSL2 Ubuntu) 학습 전 과정 — manual/02_학습_실행_매뉴얼.md 의 단계를 그대로 이은 것.
#
#   REV=2026-12-05_r1 bash scripts/pipeline.sh            # 처음부터 끝까지
#   REV=2026-12-05_r1 FROM=7 bash scripts/pipeline.sh     # 7단계(ONNX 내보내기)부터 다시
#
# 결과: bundles/$REV/ (젯슨에 올릴 묶음) + bundles/$REV.tar
# 필요: .venv 활성화(source .venv/bin/activate), edge/ 에서 npm ci 완료, ~/src/laya-onnx 클론
set -euo pipefail
cd "$(dirname "$0")/.."

REV="${REV:?REV=<revision> 을 주세요. 예: REV=2026-12-05_r1}"
FROM="${FROM:-0}"
RUN="runs/$REV"
DATA="${DATA:-data/dataset}"
BASE="${BASE:-models/base}"
EXPORTER="${EXPORTER:-$HOME/src/laya-onnx/export/export_onnx.py}"
PORT_FP32=8101
PORT_INT8=8102
PY="python -m helpai_decide"
mkdir -p "$RUN"

step() { local n=$1; shift; if [ "$n" -ge "$FROM" ]; then echo; echo "━━━ [$n] $*"; return 0; fi; return 1; }
PIDS=()
cleanup() { for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done; }
trap cleanup EXIT

serve() {     # serve <모델폴더> <포트> — 집 PC 에서 젯슨과 같은 런타임(edge/server.mjs)으로 띄운다
  LAYA_MODEL_DIR="$(realpath "$1")" LAYA_ALLOW_UNBUNDLED=1 LAYA_THREADS="${LAYA_THREADS:-8}" PORT="$2" HOST=127.0.0.1 \
    node edge/server.mjs > "$RUN/server_$2.log" 2>&1 &
  PIDS+=($!)
  for _ in $(seq 1 180); do
    if curl -sf "http://127.0.0.1:$2/health" > /dev/null; then echo "  서버 :$2 준비"; return 0; fi
    sleep 1
  done
  echo "  ✗ 서버 :$2 가 3 분 안에 뜨지 않음 — $RUN/server_$2.log 확인"; exit 1
}

step 0 "환경 점검"            && $PY check-env
step 1 "기반 체크포인트"       && { [ -f "$BASE/rl_agent_config.json" ] && echo "  이미 있음: $BASE" || $PY download-base --out "$BASE"; }
step 2 "데이터셋 만들기"       && $PY dataset build --config configs/dataset.yaml --out "$DATA"
step 3 "데이터셋 형식 검사"     && for s in train val test; do $PY dataset check --data "$DATA/$s.jsonl"; done
step 4 "state 토큰 수"        && $PY token-count --tokenizer "$BASE/tokenizer" --data "$DATA/train.jsonl"
step 5 "미세조정"             && $PY train --config configs/training.yaml --data "$DATA" --base "$BASE" --out "$RUN/model"
step 6 "ONNX 내보내기(fp32)"   && $PY export-onnx --model-dir "$RUN/model" --exporter "$EXPORTER" --out "$RUN/onnx_fp32" --base "$BASE"
step 7 "INT8 변환"            && $PY convert --src "$RUN/onnx_fp32" --out "$RUN/onnx_int8" --precision int8
if step 8 "fp32·INT8 예측 (edge 서버)"; then
  serve "$RUN/onnx_fp32" $PORT_FP32
  serve "$RUN/onnx_int8" $PORT_INT8
  for s in val test; do
    $PY predict --endpoint "http://127.0.0.1:$PORT_FP32" --key-env NONE --data "$DATA/$s.jsonl" --out "$RUN/pred_${s}_fp32.jsonl"
    $PY predict --endpoint "http://127.0.0.1:$PORT_INT8" --key-env NONE --data "$DATA/$s.jsonl" --out "$RUN/pred_${s}_int8.jsonl"
  done
  cleanup; PIDS=()
fi
step 9  "fp32 ↔ INT8 일치"     && $PY compare --a "$RUN/pred_test_fp32.jsonl" --b "$RUN/pred_test_int8.jsonl" | tee "$RUN/compare.json" | tail -1
step 10 "온도 보정 (val, INT8)" && $PY calibrate --preds "$RUN/pred_val_int8.jsonl" --out "$RUN/calibration.yaml"
step 11 "평가 (test, INT8)"     && $PY evaluate --preds "$RUN/pred_test_int8.jsonl" --calibration "$RUN/calibration.yaml" --out "$RUN/eval_report.json"
if step 12 "묶음 만들기"; then
  rm -rf "bundles/$REV"
  $PY bundle make --onnx-dir "$RUN/onnx_int8" --calibration "$RUN/calibration.yaml" --report "$RUN/eval_report.json" \
      --out "bundles/$REV" --revision "$REV" --precision int8 --exporter-dir "$(dirname "$(dirname "$EXPORTER")")" \
      --dataset-info "$DATA/dataset_info.json"
  $PY bundle verify "bundles/$REV"
  tar -C bundles -cf "bundles/$REV.tar" "$REV"
  echo; echo "완료 → bundles/$REV.tar  ($(du -h "bundles/$REV.tar" | cut -f1))  — manual/03 으로 젯슨에 올린다"
fi
if step 13 "작은 보고서를 reports/ 로 (git 으로 공유)"; then
  mkdir -p "reports/$REV"
  for f in "$RUN/eval_report.json" "$RUN/compare.json" "$RUN/calibration.yaml" "$RUN/model/train_info.json" \
           "bundles/$REV/manifest.json" "$DATA/dataset_info.json"; do
    [ -f "$f" ] && cp "$f" "reports/$REV/"
  done
  echo "  reports/$REV/ — git add reports/$REV && git commit 으로 학교에 가져간다 (모델 파일은 tar 로)"
fi
