#!/usr/bin/env bash
# 服务器一键拉取 + 重启
# 用法：bash /root/qqchat/deploy.sh
set -e
cd /root/qqchat

echo "=== 1. 拉取最新代码 ==="
git fetch origin main
BEFORE=$(git rev-parse HEAD)
git reset --hard origin/main
AFTER=$(git rev-parse HEAD)

if [ "$BEFORE" = "$AFTER" ]; then
    echo "代码已是 ${AFTER:0:8}，仍重启以应用改动"
else
    echo "更新: ${BEFORE:0:8} -> ${AFTER:0:8}"
    git log --oneline "$BEFORE..$AFTER" | head -10
fi

echo "=== 2. 语法检查 ==="
if python3 -m py_compile app/main.py app/config.py app/core/*.py app/llm/*.py app/memory/*.py 2>/dev/null; then
    echo "语法 OK"
else
    echo "语法检查失败，回滚到 $BEFORE"
    git reset --hard "$BEFORE"
    exit 1
fi

# 判断是否有需要重建镜像的改动（Dockerfile / requirements）
REBUILD=0
if [ "$BEFORE" != "$AFTER" ]; then
    if git diff --name-only "$BEFORE" "$AFTER" | grep -qE '^(app/(Dockerfile|requirements\.txt)|docker-compose\.yml)$'; then
        REBUILD=1
    fi
fi

echo "=== 3. 应用改动 ==="
if [ "$REBUILD" = "1" ]; then
    echo "检测到依赖/镜像改动，重新构建..."
    docker compose up -d --build app
else
    echo "仅代码改动，重启容器..."
    # restart 才能真正重新加载 Python 模块（up -d 对运行中容器是空操作）
    docker compose restart app
fi
sleep 10

echo "=== 4. 健康检查 ==="
for i in 1 2 3 4 5 6; do
    if curl -sf -m 5 http://127.0.0.1:8080/api/chat/health > /dev/null 2>&1; then
        echo "服务正常"
        curl -s -m 5 http://127.0.0.1:8080/api/chat/health
        echo
        exit 0
    fi
    sleep 3
done

echo "健康检查失败，查看日志："
docker logs qqchat-app --tail 30
exit 1
