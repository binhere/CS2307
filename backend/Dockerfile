# Purpose: production image of the FastAPI backend, used by Docker Compose and the homelab cluster.
# Input: the backend/ directory as build context.
# Output: gunicorn + uvicorn on port 8000, running as the unprivileged user 10001. It writes only to
#   /tmp (gunicorn heartbeat files), so it runs with a read-only root filesystem. Logs go to stderr;
#   set LOG_FILE_PATH to also write a file. Migrations are not run on start: run
#   `alembic upgrade head` with this image first (Compose service "migrate", a Kubernetes initContainer).

FROM python:3.11.18-slim-trixie

RUN ln -snf /usr/share/zoneinfo/Asia/Ho_Chi_Minh /etc/localtime && echo Asia/Ho_Chi_Minh > /etc/timezone

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# The root filesystem is read-only at runtime, so compile the bytecode now instead of on first import.
RUN python -m compileall -q src migrations && mkdir -p logs && chown 10001:10001 logs

USER 10001:10001
EXPOSE 8000
CMD ["gunicorn", "src.main:app", "--workers", "1", "--timeout", "300", "--worker-class", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8000"]
