FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN groupadd -g 10001 planner && useradd -u 10001 -g planner -M planner
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY --chown=planner:planner app ./app
COPY --chown=planner:planner migrations ./migrations
COPY --chown=planner:planner scripts ./scripts
COPY --chown=planner:planner main.py alembic.ini ./
RUN mkdir -p data logs && chown -R planner:planner data logs && chmod +x scripts/entrypoint.sh
USER 10001:10001
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 CMD ["python", "scripts/healthcheck.py"]
ENTRYPOINT ["/app/scripts/entrypoint.sh"]
