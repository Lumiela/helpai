# -*- coding: utf-8 -*-
"""
집 PC(학습 장비) 전용 도구 — torch·laya·onnx 가 필요하다. 젯슨에서는 쓰지 않는다.

  check_env      학습 환경 점검 (Python·CUDA·RTX 50 계열 sm_120·laya API 이름)
  download_base  기반 체크포인트 내려받기 (convaiinnovations/laya, 기본 typed-decisions)
  train          laya.train.finetune 호출 (설정 = configs/training.yaml)
  export_onnx    receptron/laya 의 export/export_onnx.py 로 ONNX(fp32) 내보내기
  convert        fp32 ONNX → INT8(동적 양자화) / FP16
  token_count    state 토큰 수 실측 (예산 확인)

laya 의 학습 API 는 판마다 조금씩 다르다(2026-11-27 확인 판: laya 0.5.5 — laya.train.TrainConfig·finetune·dry_run·save_checkpoint).
이름이 바뀌었으면 check_env 출력을 보고 이 파일만 고치면 된다.
"""
import dataclasses
import inspect
import json
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path


def _ver(mod):
    try:
        m = __import__(mod)
        return getattr(m, "__version__", "ok")
    except Exception as e:
        return "없음 (%s)" % e.__class__.__name__


# ───────────────────────────────────────── check-env
def check_env():
    ok = True
    print("Python      %s (%s)" % (sys.version.split()[0], platform.platform()))
    if sys.version_info < (3, 10):
        print("  ✗ laya 는 Python 3.10 이상이 필요하다")
        ok = False
    for m in ("transformers", "huggingface_hub", "safetensors", "onnx", "onnxscript", "onnxruntime", "tokenizers"):
        print("%-11s %s" % (m, _ver(m)))
    try:
        import torch
        print("torch       %s  (CUDA 빌드 %s)" % (torch.__version__, torch.version.cuda))
        if not torch.cuda.is_available():
            print("  ✗ torch 가 GPU 를 못 본다 — cu128 휠인지, 드라이버가 WSL 에 보이는지(nvidia-smi) 확인")
            ok = False
        else:
            name = torch.cuda.get_device_name(0)
            cap = torch.cuda.get_device_capability(0)
            mem = torch.cuda.get_device_properties(0).total_memory / 2 ** 30
            arch = torch.cuda.get_arch_list()
            print("GPU         %s  sm_%d%d  %.1f GB" % (name, cap[0], cap[1], mem))
            print("torch arch  %s" % " ".join(arch))
            if "sm_%d%d" % cap not in arch and "compute_%d%d" % cap not in arch:
                print("  ✗ 이 torch 는 sm_%d%d 를 지원하지 않는다 — RTX 50 계열은 CUDA 12.8 용(cu128) torch 2.7 이상" % cap)
                ok = False
            x = torch.randn(256, 256, device="cuda")
            torch.cuda.synchronize()
            print("  ✓ GPU 연산 시험 통과 (%.3f)" % float((x @ x).mean()))
            print("  fp16 autocast: 지원" + ("" if cap >= (7, 0) else " 안 됨"))
    except ImportError:
        print("torch       없음 — 매뉴얼 01 의 cu128 설치 명령")
        ok = False
    try:
        import laya
        print("laya        %s" % getattr(laya, "__version__", "?"))
        from laya import train as lt
        tc = getattr(lt, "TrainConfig", None)
        if tc is None:
            print("  ✗ laya.train.TrainConfig 없음 — 판이 바뀌었다. laya_tools.train 수정 필요")
            ok = False
        else:
            print("  TrainConfig 필드: %s" % ", ".join(f.name for f in dataclasses.fields(tc)))
        for fn in ("finetune", "dry_run", "save_checkpoint", "load_checkpoint"):
            f = getattr(lt, fn, None)
            print("  laya.train.%-16s %s" % (fn, inspect.signature(f) if f else "없음"))
        ag = getattr(laya, "Agent", None)
        if ag:
            print("  laya.Agent 메서드: %s" % ", ".join(m for m in dir(ag) if not m.startswith("_")))
        for fn in ("fit_temperature_map", "calibration_records"):
            try:
                import laya.calibrate as lc
                print("  laya.calibrate.%-20s %s" % (fn, "있음" if hasattr(lc, fn) else "없음"))
            except ImportError:
                pass
    except ImportError:
        print("laya        없음 — pip install -r requirements-train.txt")
        ok = False
    print("\n결과: %s" % ("통과" if ok else "문제 있음 (위 ✗ 확인)"))
    return ok


# ───────────────────────────────────────── download-base
def download_base(out_dir, repo="convaiinnovations/laya", subfolder="typed-decisions", revision=None):
    from huggingface_hub import snapshot_download

    out = Path(out_dir)
    if subfolder:
        patterns = ["%s/*" % subfolder, "%s/**" % subfolder, "*.py", "*.json", "*.md"]
    else:
        patterns = ["model.safetensors", "*.json", "*.py", "*.md", "encoder/*", "tokenizer/*"]
    snap = Path(snapshot_download(repo_id=repo, revision=revision, allow_patterns=patterns))
    src = snap / subfolder if subfolder else snap
    cands = [p.parent for p in src.rglob("rl_agent_config.json")]
    if not cands:
        raise SystemExit("rl_agent_config.json 을 찾지 못했다: %s (내려받은 파일: %s)" %
                         (src, [str(p.relative_to(snap)) for p in snap.rglob("*") if p.is_file()][:40]))
    model_dir = cands[0]
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(model_dir, out)
    if not (out / "rl_common.py").exists():
        rc = sorted(snap.rglob("rl_common.py"), key=lambda p: len(p.parts))
        if rc:
            shutil.copy2(rc[0], out / "rl_common.py")
        else:
            print("  ⚠ rl_common.py 가 저장소에 없다 — ONNX 내보내기 때 필요할 수 있다")
    info = {"repo": repo, "subfolder": subfolder, "revision": revision, "snapshot": str(snap),
            "downloaded_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    (out / "base_info.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    print("기반 체크포인트 → %s" % out)
    for p in sorted(out.rglob("*")):
        if p.is_file():
            print("  %-50s %8.1f MB" % (p.relative_to(out), p.stat().st_size / 2 ** 20))
    return out


# ───────────────────────────────────────── train
def train(data_dir, base_dir, out_dir, cfg, device="auto"):
    from laya import train as lt

    data_dir, base_dir, out_dir = Path(data_dir), Path(base_dir), Path(out_dir)
    tr, va = data_dir / "train.jsonl", data_dir / "val.jsonl"
    if not tr.exists():
        raise SystemExit("학습 데이터가 없다: %s (먼저 dataset build)" % tr)
    names = {f.name for f in dataclasses.fields(lt.TrainConfig)}
    kw = {k: v for k, v in (cfg or {}).items() if k in names}
    unknown = sorted(set(cfg or {}) - names)
    if unknown:
        print("  ⚠ 이 laya 판의 TrainConfig 에 없는 설정은 무시: %s" % ", ".join(unknown))
    if "eval_data" in names and va.exists() and "eval_data" not in kw:
        kw["eval_data"] = str(va)
    config = lt.TrainConfig(**kw)
    print("TrainConfig: %s" % config)
    if hasattr(lt, "dry_run"):
        print("dry_run → %s" % (lt.dry_run(str(tr), str(base_dir), config=config),))
    sig = inspect.signature(lt.finetune)
    args = {"data": str(tr), "model_dir": str(base_dir), "output_dir": str(out_dir), "config": config,
            "device": device}
    call = {k: v for k, v in args.items() if k in sig.parameters}
    missing = [p for p, v in sig.parameters.items() if v.default is inspect._empty and p not in call]
    if missing:
        raise SystemExit("laya.train.finetune%s 의 필수 인자 %s 를 모른다 — laya_tools.train 수정 필요" % (sig, missing))
    t0 = time.time()
    result = lt.finetune(**call)
    dt = time.time() - t0
    if (base_dir / "rl_common.py").exists() and not (out_dir / "rl_common.py").exists():
        shutil.copy2(base_dir / "rl_common.py", out_dir / "rl_common.py")   # ONNX 내보내기가 model_dir 에서 import 한다
    info = {"seconds": round(dt, 1), "config": {k: (str(v) if not isinstance(v, (int, float, str, bool, type(None)))
                                                     else v) for k, v in kw.items()},
            "laya": __import__("laya").__dict__.get("__version__"), "result": repr(result)[:2000]}
    (out_dir / "train_info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    print("학습 끝 %.1f 분 → %s" % (dt / 60, out_dir))
    return out_dir


# ───────────────────────────────────────── export-onnx
def export_onnx(model_dir, exporter, out_dir, base_dir=None):
    model_dir, out_dir, exporter = Path(model_dir).resolve(), Path(out_dir).resolve(), Path(exporter).resolve()
    if not exporter.exists():
        raise SystemExit("export_onnx.py 가 없다: %s (매뉴얼 02 의 git clone 단계)" % exporter)
    if not (model_dir / "rl_common.py").exists():
        src = Path(base_dir) / "rl_common.py" if base_dir else None
        if src and src.exists():
            shutil.copy2(src, model_dir / "rl_common.py")
        else:
            print("  ⚠ %s 에 rl_common.py 가 없다 — 내보내기가 import 에서 실패할 수 있다" % model_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(exporter), str(model_dir), str(out_dir)]
    print("$ " + " ".join(cmd))
    subprocess.check_call(cmd, cwd=str(exporter.parent))
    for f in ("laya.onnx", "laya_config.json", "tokenizer"):
        if not (out_dir / f).exists():
            raise SystemExit("내보내기 결과에 %s 가 없다" % f)
    print("ONNX(fp32) → %s  (%.0f MB)" % (out_dir, (out_dir / "laya.onnx").stat().st_size / 2 ** 20))
    return out_dir


# ───────────────────────────────────────── convert (int8 / fp16)
def convert(src_dir, out_dir, precision="int8"):
    src, out = Path(src_dir), Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for p in src.iterdir():
        if p.name in ("laya.onnx", "laya.onnx.data"):
            continue
        (shutil.copytree if p.is_dir() else shutil.copy2)(p, out / p.name)
    inp, dst = src / "laya.onnx", out / "laya.onnx"
    if precision == "int8":
        from onnxruntime.quantization import QuantType, quantize_dynamic
        pre = out / "_pre.onnx"
        try:
            from onnxruntime.quantization.shape_inference import quant_pre_process
            quant_pre_process(str(inp), str(pre), skip_symbolic_shape=True)
            source = pre
        except Exception as e:
            print("  ⚠ 전처리 생략 (%s) — 원본으로 양자화" % e.__class__.__name__)
            source = inp
        quantize_dynamic(str(source), str(dst), weight_type=QuantType.QInt8)
        if pre.exists():
            pre.unlink()
    elif precision == "fp16":
        import onnx
        from onnxconverter_common import float16
        m = onnx.load(str(inp))
        m16 = float16.convert_float_to_float16(m, keep_io_types=True)
        onnx.save(m16, str(dst))
    else:
        raise SystemExit("precision 은 int8 | fp16")
    print("%s → %s (%.0f MB)" % (precision.upper(), dst, dst.stat().st_size / 2 ** 20))
    return out


# ───────────────────────────────────────── token-count
def token_count(tokenizer_dir, rows_path, limit=500):
    from tokenizers import Tokenizer
    from .dataset import read_rows

    tok = Tokenizer.from_file(str(Path(tokenizer_dir) / "tokenizer.json"))
    ns = []
    for i, row in enumerate(read_rows(rows_path)):
        if i >= limit:
            break
        ns.append(len(tok.encode(json.dumps(row["state"], ensure_ascii=False)).ids))
    ns.sort()
    if not ns:
        print("행 없음")
        return
    print("state 토큰 수 (%d 행): 최소 %d · 중앙 %d · p95 %d · 최대 %d"
          % (len(ns), ns[0], ns[len(ns) // 2], ns[int(len(ns) * 0.95) - 1], ns[-1]))
