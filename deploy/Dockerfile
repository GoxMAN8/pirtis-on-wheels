# One image, two workloads: the playground (the default command) and the nightly gate
# job (`gate-job`). Built from the repo root:
#
#   docker build -f deploy/Dockerfile -t pirtis .
#
# The repo's layout is kept under /app (python/, fixtures/, tests/human/): the code
# finds the fixtures and the scenario cards relative to itself. What enters the build
# is an allow-list in the root .dockerignore; .env never does.

# Both base images are pinned by digest; bump them together, on purpose.
FROM ghcr.io/astral-sh/uv:0.9.28@sha256:59240a65d6b57e6c507429b45f01b8f2c7c0bbeee0fb697c41a39c6a8e3a4cfb AS uv

# build: the locked environment, made with uv, which stays in this stage
FROM python:3.12.13-slim@sha256:229a2c5bfa27522db7815ea81f9bed70af17ccb9de9fc7ad142b1877b5830d36 AS build
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.12
WORKDIR /app/python
# the locked dependencies first, so a code change reuses this layer
COPY python/pyproject.toml python/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY python/ ./
RUN uv sync --locked --no-dev \
 && .venv/bin/python -m compileall -q pirtis

# run: the same base, the environment and the code, no build tools
FROM python:3.12.13-slim@sha256:229a2c5bfa27522db7815ea81f9bed70af17ccb9de9fc7ad142b1877b5830d36
# code owned by root and read-only to the app; the app writes only to /data and /tmp
RUN useradd --system --uid 10001 --no-create-home --home-dir /nonexistent pirtis \
 && mkdir /data && chown pirtis /data
COPY --from=build /app/python /app/python
COPY fixtures/ /app/fixtures/
COPY tests/human/ /app/tests/human/
WORKDIR /app/python
USER 10001

# PIRTIS_PROMOTE_DIR is empty: a promoted case downloads instead of being written (D-66).
# PIRTIS_AUTH=required: without accounts (PIRTIS_USERS) the playground refuses to start (D-83).
ENV PATH=/app/python/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIRTIS_HOST=0.0.0.0 \
    PORT=8000 \
    PIRTIS_DATA_DIR=/data \
    PIRTIS_PROMOTE_DIR="" \
    PIRTIS_AUTH=required

EXPOSE 8000
CMD ["playground"]
