# Demo image: synthetic data only, no review store, no API key needed.
#
# The default command serves the synthetic workbench so the page and the three
# workflows are clickable on any host. Real reviews stay outside the image: the
# `local` profile in docker-compose.yml mounts them read-write because runs write
# checkpoints and human check records back into data/processed/.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

COPY scripts ./scripts
COPY artifacts/manifests ./artifacts/manifests

RUN useradd --create-home --uid 10001 workbench \
    && mkdir -p /app/data /app/state \
    && chown -R workbench:workbench /app
USER workbench

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

CMD ["python", "scripts/serve_demo.py", \
     "--host", "0.0.0.0", \
     "--allow-non-loopback", \
     "--port", "8000", \
     "--mode", "synthetic"]
