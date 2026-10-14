# 스마트큐브 API — 젯슨 실험용. compose 의 api 서비스가 이 폴더를 build 한다.
# 젯슨(aarch64)에서 그대로 빌드된다. 중계기(bridge)와 같은 방식.
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# 8000 은 nginx(web) 가 내부망에서 붙는 포트. 호스트로는 내보내지 않는다.
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
