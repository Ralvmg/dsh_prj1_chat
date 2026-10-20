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
    echo "已是最新版本: ${AFTER:0:8}"
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

echo "=== 3. 重启容器 ==="
docker compose up -d app
sleep 8

echo "=== 4. 健康检查 ==="
for i in 1 2 3 4 5; do
    if curl -sf -m 5 http://127.0.0.1:8080/api/chat/health > /dev/null 2>&1; then
        echo "服务正常"
        curl -s -m 5 http://127.0.0.1:8080/api/chat/health
        echo
        exit 0
    fi
    sleep 3
done

echo "健康检查失败，查看日志："
docker logs qqchat-app --tail 20
exit 1
