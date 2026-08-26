FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_ENV=dev

# 系统依赖（最小集）
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

# 先只拷依赖文件，利用 Docker 缓存层
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 代码通过 volume 挂载，这里不 COPY

EXPOSE 8090

# 保持容器运行，服务启动命令在容器内手动执行
CMD ["sleep", "infinity"]
