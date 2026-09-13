FROM node:22-slim AS dashboard
WORKDIR /dashboard
COPY apps/dashboard/package*.json ./
RUN npm ci --no-audit --no-fund
COPY apps/dashboard ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY apps/backend apps/backend
COPY --from=dashboard /dashboard/dist apps/dashboard/dist
COPY packages packages
RUN pip install --no-cache-dir uv==0.11.14 \
    && uv sync --locked --no-dev --no-editable --no-cache \
    && useradd --create-home traffic
ENV PATH="/app/.venv/bin:$PATH"
USER traffic
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready')"
CMD ["uvicorn", "smart_traffic_backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
