FROM node:25-slim AS dashboard
WORKDIR /dashboard
COPY apps/dashboard/package*.json ./
RUN npm ci --no-audit --no-fund
COPY apps/dashboard ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY apps/backend apps/backend
COPY apps/dashboard/src apps/dashboard/src
COPY --from=dashboard /dashboard/dist apps/dashboard/dist
COPY packages packages
COPY configs configs
COPY docs docs
COPY tests/test_maket_hil.py tests/test_maket_hil.py
RUN pip install --no-cache-dir uv==0.11.14 \
    && uv sync --locked --extra learning --extra pilot --no-dev --no-editable --no-cache \
    && useradd --create-home traffic \
    && mkdir -p /app/.runtime /app/artifacts/rohai-ai-2.0/stage3 \
    && chown -R traffic:traffic /app/.runtime /app/artifacts
ENV PATH="/app/.venv/bin:$PATH"
USER traffic
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready')"
CMD ["uvicorn", "smart_traffic_backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
