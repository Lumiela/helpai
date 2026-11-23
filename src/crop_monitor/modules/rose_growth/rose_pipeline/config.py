from pathlib import Path

import yaml

# crop_monitor 에 포함하면서 프로젝트 루트(Image_Langgraph)를 기준으로 바꿈
# rose_pipeline → rose_growth → modules → crop_monitor → src → 프로젝트 루트
PROJECT_ROOT = Path(__file__).resolve().parents[5]


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


def load_config(path="config/rose/growth.yaml"):
    cfg = load_yaml(resolve(path))
    path = resolve(cfg["holes_file"])
    cfg["holes"] = load_yaml(path) if path.exists() else {}
    return cfg
