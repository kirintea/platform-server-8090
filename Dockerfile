FROM python:3.12-slim

LABEL version="0.1.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_ENV=dev \
    IMAGE_VERSION=0.1.0

# 换 apt 国内源
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources

# 系统依赖（最小集）
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

# 先只拷依赖文件，利用 Docker 缓存层
COPY requirements.txt .
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    && pip install --no-cache-dir -r requirements.txt

# 拷贝应用代码（利用 .dockerignore 排除无关文件）
COPY main.py server.py ./
COPY api/ api/
COPY core/ core/
COPY middleware/ middleware/
COPY configs/ configs/
COPY health_check/ health_check/
COPY scripts/ scripts/

# workspaces/ 和 skills/ 通过 volume 挂载，不 COPY

EXPOSE 8090

# 默认启动服务（生产模式）
# 开发模式下 docker-compose 可覆盖为 ["sleep", "infinity"]
CMD ["python", "main.py"]
