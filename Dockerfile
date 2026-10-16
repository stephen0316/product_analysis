FROM node:22-bookworm-slim AS frontend-build
WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS runtime
ARG APP_REVISION=local
ARG APP_SOURCE=https://github.com/stephen0316/product_analysis
LABEL org.opencontainers.image.source=${APP_SOURCE} \
      org.opencontainers.image.revision=${APP_REVISION}
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PRODUCT_EVAL_DATA_DIR=/app/local_data \
    APP_REVISION=${APP_REVISION} \
    TZ=Asia/Shanghai
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates fonts-noto-cjk tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 evaluator \
    && useradd --uid 10001 --gid evaluator --no-create-home evaluator
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY post_evaluation/ ./post_evaluation/
COPY --from=frontend-build /build/frontend/dist ./frontend/dist
RUN mkdir -p /app/local_data && chown 10001:10001 /app/local_data
USER 10001:10001
EXPOSE 8765
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=6 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=4).close()"
CMD ["python", "-m", "uvicorn", "post_evaluation.app:app", "--host", "0.0.0.0", "--port", "8765", "--workers", "1"]
