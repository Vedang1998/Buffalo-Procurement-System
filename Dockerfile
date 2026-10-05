# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e

FROM ghcr.io/astral-sh/uv:0.12.3@sha256:2d890623d310b57771ce840f0da5eed5fc6d657da05ffaa45d82797b53fa3abc AS uv

FROM python:3.13.11-slim-bookworm@sha256:20080e807bfc404f8450b185cf0fc95d553462673598549613735f70a5b4d5d0 AS dependencies

ENV PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/buffalo-venv

COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /build
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

FROM python:3.13.11-slim-bookworm@sha256:20080e807bfc404f8450b185cf0fc95d553462673598549613735f70a5b4d5d0 AS runtime

RUN rm -f /etc/apt/sources.list.d/debian.sources \
    && printf '%s\n' \
        "deb [check-valid-until=no] https://snapshot.debian.org/archive/debian/20260202T000000Z/ bookworm main" \
        "deb [check-valid-until=no] https://snapshot.debian.org/archive/debian/20260202T000000Z/ bookworm-updates main" \
        "deb [check-valid-until=no] https://snapshot.debian.org/archive/debian-security/20260202T000000Z/ bookworm-security main" \
        > /etc/apt/sources.list \
    && apt-get update \
    && apt-get install --yes --no-install-recommends \
        "git=1:2.39.5-0+deb12u3" \
        "tini=0.19.0-1+b3" \
    && test "$(dpkg-query -W -f='${Version}' git)" = '1:2.39.5-0+deb12u3' \
    && test "$(dpkg-query -W -f='${Version}' tini)" = '0.19.0-1+b3' \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 1201 buffalo-gateway \
    && groupadd --gid 1202 buffalo-synthetic \
    && groupadd --gid 1203 buffalo-research \
    && groupadd --gid 2301 buffalo-synthetic-socket \
    && groupadd --gid 2302 buffalo-research-socket \
    && groupadd --gid 2303 buffalo-control-socket \
    && useradd --uid 1101 --gid 1201 --no-create-home \
        --home-dir /run/buffalo-staging/gateway \
        --shell /usr/sbin/nologin buffalo-gateway \
    && useradd --uid 1102 --gid 1202 --no-create-home \
        --home-dir /run/buffalo-staging/synthetic \
        --shell /usr/sbin/nologin buffalo-synthetic \
    && useradd --uid 1103 --gid 1203 --no-create-home \
        --home-dir /run/buffalo-staging/research \
        --shell /usr/sbin/nologin buffalo-research \
    && usermod --append --groups \
        buffalo-synthetic-socket,buffalo-research-socket,buffalo-control-socket \
        buffalo-gateway \
    && usermod --append --groups buffalo-synthetic-socket buffalo-synthetic \
    && usermod --append --groups buffalo-research-socket buffalo-research

COPY --from=dependencies /opt/buffalo-venv /opt/buffalo-venv

RUN install --directory --owner=0 --group=0 --mode=0755 \
        /app \
        /app/procurement \
        /data \
    && printf '%s\n' '/app/procurement/src' \
        > /opt/buffalo-venv/lib/python3.13/site-packages/buffalo-procurement-os.pth \
    && chown 0:0 \
        /opt/buffalo-venv/lib/python3.13/site-packages/buffalo-procurement-os.pth \
    && chmod 0644 \
        /opt/buffalo-venv/lib/python3.13/site-packages/buffalo-procurement-os.pth

COPY procurement/src /app/procurement/src
COPY procurement/config /app/procurement/config
COPY procurement/db /app/procurement/db
COPY procurement/seed/variant_aliases.csv /app/procurement/seed/variant_aliases.csv
COPY procurement/review/phase4_identity_manifest_corrected.csv /app/procurement/review/phase4_identity_manifest_corrected.csv
COPY procurement/review/phase4_terminal_disposition_manifest.csv /app/procurement/review/phase4_terminal_disposition_manifest.csv

RUN find /app /opt/buffalo-venv -xdev -type d -exec chmod go-w {} + \
    && find /app /opt/buffalo-venv -xdev -type f -exec chmod go-w {} + \
    && test "$(stat -c '%u:%g:%a' /app)" = '0:0:755' \
    && test "$(stat -c '%u:%g:%a' /data)" = '0:0:755' \
    && test "$(stat -c '%u:%g:%a' /usr/bin/git)" = '0:0:755'

ENV BUFFALO_STAGING_VOLUME_ROOT=/data \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PATH=/opt/buffalo-venv/bin:/usr/local/bin:/usr/bin:/bin \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=UTC

WORKDIR /app
USER 0:0
STOPSIGNAL SIGTERM
ENTRYPOINT ["/usr/bin/tini", "-g", "--", "/opt/buffalo-venv/bin/python", "-I", "-B", "-m", "procurement_os.staging_bootstrap"]
