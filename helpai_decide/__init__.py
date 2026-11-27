# -*- coding: utf-8 -*-
"""
헬피아이 결정 모델 키트 (helpai_decide).

    젯슨 InfluxDB 의 센서 이력 → 판정 문장(state) → Laya 결정 모델 학습·보정·ONNX 묶음 → 젯슨 laya-edge 추론.

이 패키지의 핵심(profile·features·questions·labeler·calibration·bundle)은 pyyaml 외 의존성이 없다.
rules 엔진(젯슨)이 추론 때 같은 특징·질문 코드를 그대로 가져다 쓰게 하기 위해서다.
학습 도구(laya_tools)·Influx 내보내기(influx_export)만 무거운 의존성(torch·laya·influxdb-client)을 쓴다.
"""
__version__ = "0.1.0"
