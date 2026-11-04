# 營運系統（餐點點餐、營運後台、webhook、備份）共用的映像檔
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    OPS_DB=/data/ops.db

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# 不用 root 執行；資料庫放在 /data（compose 會掛載成持久儲存）
RUN useradd -m app && mkdir -p /data /backups && chown -R app /data /backups /app
USER app
VOLUME ["/data", "/backups"]
