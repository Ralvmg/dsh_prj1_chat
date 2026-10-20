FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

WORKDIR /app

# 先装依赖（利用缓存）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 代码复制到 /app/app/，这样 python -m app.main 能找到包
COPY . /app/app/

# 数据目录
RUN mkdir -p /app/data /app/logs

CMD ["python", "-m", "app.main"]
