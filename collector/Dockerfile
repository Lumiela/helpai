# shadow 수집기 — 젯슨(arm64)·x86 공용. 의존성은 pyyaml·tzdata 뿐 (torch·laya 불필요).
# 빌드 맥락 = 저장소 루트:  cd <저장소>/edge && docker compose build collector
FROM python:3.12-slim

WORKDIR /app
RUN pip install --no-cache-dir "pyyaml>=6,<7" tzdata
COPY helpai_decide/ ./helpai_decide/
COPY configs/ ./configs/
COPY profiles/ ./profiles/

ENV PYTHONUNBUFFERED=1 TZ=Asia/Seoul
USER nobody
CMD ["python", "-m", "helpai_decide", "collect", "--config", "/app/configs/collector.yaml"]
