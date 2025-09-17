FROM python:3.12-slim

# 安装curl用于健康检查
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

# 设置环境变量
ENV PYTHONUNBUFFERED=1

# 设置工作目录
WORKDIR /app

# 复制依赖文件并安装
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY k2think_proxy.py .
COPY src/ ./src/
COPY templates ./templates/

# 创建一个默认的空tokens.txt文件（如果没有通过volume挂载的话）
RUN touch tokens_guest.txt
RUN touch tokens.txt && echo "# 请通过docker-compose或volume挂载实际的tokens.txt文件" > tokens.txt
RUN chmod 644 ./tokens_guest.txt ./tokens.txt

# 创建非root用户运行应用
RUN useradd -r -s /bin/false appuser && \
    chown -R appuser:appuser /app
USER appuser

# 暴露端口
EXPOSE 8001

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:8001/health || exit 1

# 启动应用
CMD ["python", "k2think_proxy.py"]