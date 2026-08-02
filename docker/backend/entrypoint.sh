#!/bin/sh
set -e

# 重试 init_db.py，最多等 MySQL 30 秒
echo "Waiting for MySQL to be ready..."
for i in $(seq 1 30); do
    if python sql/init_db.py 2>/dev/null; then
        echo "MySQL is ready."
        break
    fi
    echo "MySQL not ready yet (attempt $i/30), waiting 2s..."
    sleep 2
done

# 最后一次尝试，如果失败则真正退出（不再静默）
echo "Final attempt to initialize database..."
python sql/init_db.py

exec uvicorn main:app --host "${FASTAPI_HOST:-0.0.0.0}" --port "${FASTAPI_PORT:-8000}"
