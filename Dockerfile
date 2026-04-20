FROM python:3.11-slim

WORKDIR /app

# 시스템 의존성 (web3.py가 gmp/sodium 바인딩 요구)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libgmp-dev \
        libsodium-dev \
    && rm -rf /var/lib/apt/lists/*

# Python 의존성만 먼저 설치 (레이어 캐싱)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 소스
COPY . .

# 이벤트 DB 경로는 볼륨으로 마운트
VOLUME /app/state

# 기본 실행: controller 모드, 자동 승인
# 실제 지갑 주소는 런타임에 -e 또는 --wallet-* 인자로 주입
CMD ["python", "main.py", "--mode", "controller", "--auto"]
