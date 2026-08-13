#!/usr/bin/env bash
# ============================================================
# 启动 main.py — 自有平台层服务（端口 8090）
#
# 提供：
#   - /chat /chat/stream /sessions/* 等自有 API
#   - 旧版静态前端 http://localhost:8090/
#   - Swagger 文档   http://localhost:8090/docs
#
# 用法：
#   ./scripts/start_8090.sh
#   APP_ENV=prod ./scripts/start_8090.sh
#   VENV_PATH=/opt/venv ./scripts/start_8090.sh
#   VENV_PATH= ./scripts/start_8090.sh           # 用全局 Python（打镜像场景）
# ============================================================

set -euo pipefail

# ------------------------------------------------------------ 可配置：虚拟环境路径
#   - 默认 ".venv"（项目根目录下的虚拟环境）
#   - 改成空字符串 "" 则使用全局 Python 环境（Docker 镜像 / CI 场景）
#   - 也可改成绝对路径，如 /opt/venv
#   - 跨平台兼容：自动识别 bin/activate（Linux/macOS）和 Scripts/activate（Windows git bash）
# ------------------------------------------------------------
VENV_PATH="${VENV_PATH:-.venv}"

# ------------------------------------------------------------ 切换到项目根目录
#    脚本位于 scripts/ 下，向上回溯一层
cd "$(dirname "$0")/.."
PROJECT_ROOT="$(pwd)"
echo "[INFO] 项目根目录: $PROJECT_ROOT"

# ------------------------------------------------------------ 激活虚拟环境（如果配置了）
if [ -n "$VENV_PATH" ]; then
    if [ ! -d "$VENV_PATH" ]; then
        echo "[ERROR] 虚拟环境目录不存在: $VENV_PATH"
        echo "[HINT]  1) 创建虚拟环境: python -m venv $VENV_PATH"
        echo "        2) 安装依赖:    $VENV_PATH/bin/pip install -r requirements.txt"
        echo "        3) 或改环境变量 VENV_PATH=\"\" 使用全局 Python"
        exit 1
    fi

    if [ -f "$VENV_PATH/bin/activate" ]; then
        # Linux / macOS / WSL
        # shellcheck disable=SC1091
        source "$VENV_PATH/bin/activate"
    elif [ -f "$VENV_PATH/Scripts/activate" ]; then
        # Windows git bash（venv 在 Windows 上的目录结构是 Scripts/ 而非 bin/）
        # shellcheck disable=SC1091
        source "$VENV_PATH/Scripts/activate"
    else
        echo "[ERROR] 虚拟环境 activate 脚本未找到"
        echo "        既没有 $VENV_PATH/bin/activate，也没有 $VENV_PATH/Scripts/activate"
        exit 1
    fi
    echo "[INFO] 已激活虚拟环境: $VENV_PATH"
else
    echo "[INFO] VENV_PATH 为空，使用全局 Python: $(command -v python || command -v python3)"
fi

# ------------------------------------------------------------ 应用环境：dev / prod
APP_ENV="${APP_ENV:-dev}"
export APP_ENV
echo "[INFO] APP_ENV=$APP_ENV"

# ------------------------------------------------------------ 前置依赖检查：Redis
#    8090 的 SessionManager 强依赖 Redis（未连上会降级为纯内存，不持久化）
if command -v redis-cli >/dev/null 2>&1; then
    if redis-cli -u "${REDIS_URL:-redis://localhost:6379/0}" ping >/dev/null 2>&1; then
        echo "[INFO] Redis 连接正常"
    else
        echo "[WARN] Redis 不可达（${REDIS_URL:-redis://localhost:6379/0}），将降级为纯内存模式"
        echo "[HINT]  docker run -d --name redis-dev -p 6379:6379 redis:7-alpine"
    fi
else
    echo "[WARN] 未找到 redis-cli，跳过 Redis 连通性检查"
fi

# ------------------------------------------------------------ 加载 .env（main.py 内部也会 load_dotenv，这里只是冗余保险）
if [ -f .env ]; then
    echo "[INFO] 检测到 .env 文件"
fi

# ------------------------------------------------------------ 启动 main.py
echo "[INFO] 启动 main.py (端口 8090)..."
echo "[INFO] 前端入口: http://localhost:8090/"
echo "[INFO] Swagger:  http://localhost:8090/docs"
echo "------------------------------------------------------------"
# exec 让 python 进程直接接管当前 shell，Ctrl+C 信号能直接传到 python
exec python main.py
