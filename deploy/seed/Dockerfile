# Purpose: the one-time catalog load for a new deployment (a Kubernetes Job).
# Input: a build context assembled by deploy/seed/build_seed.sh: seed.py, requirements.txt and data/
#   (catalog/*.csv exported from PostgreSQL without vectors, graph/*.csv, import_search_graph.cypher,
#   SEED_VERSION). Do not build it from deploy/seed directly.
# Output: an image that runs seed.py as the unprivileged user 10001 with a read-only root filesystem.

FROM python:3.11.18-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SEED_DATA_DIR=/seed/data

WORKDIR /seed
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY seed.py .
COPY data/ data/

USER 10001:10001
CMD ["python", "/seed/seed.py"]
