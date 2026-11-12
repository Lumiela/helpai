from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_yaml(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_yaml(data, path, header=""):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        if header:
            f.write(header)
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False, default_flow_style=None)


def resolve(path, root=PROJECT_ROOT):
    """설정 파일의 상대 경로를 프로젝트 루트 기준 절대 경로로 변환."""
    p = Path(path)
    return p if p.is_absolute() else (Path(root) / p).resolve()


def load_config(path="configs/pipeline.yaml"):
    cfg = load_yaml(resolve(path))
    path = resolve(cfg["holes_file"])
    cfg["holes"] = load_yaml(path) if path.exists() else {}
    return cfg
