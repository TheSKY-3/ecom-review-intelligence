# Download-and-run image: ships the three-product subset so the workbench shows
# real reviews without the raw 711 MB dataset. The full category store stays out
# of the image; the `local` compose profile mounts it instead.
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
COPY data/public/ice_makers_public.sqlite ./data/public/ice_makers_public.sqlite

RUN useradd --create-home --uid 10001 workbench \
    && mkdir -p /app/data/public/state \
    && chown -R workbench:workbench /app/data
USER workbench

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

CMD ["python", "scripts/serve_demo.py", \
     "--host", "0.0.0.0", \
     "--allow-non-loopback", \
     "--port", "8000", \
     "--mode", "bundled"]

