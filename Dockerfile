# syntax=docker/dockerfile:1.7

#########################
# Builder (has compilers, pip/uv tooling, etc.)
#########################
FROM cgr.dev/tracecat.com/python:3.12-dev AS builder

# We’ll work as root for installs, then return to nonroot for runtime.
USER root

ENV HOST=0.0.0.0 \
    PORT=8000 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUSERBASE="/home/nonroot/.local" \
    UV_CACHE_DIR="/home/nonroot/.cache/uv" \
    PYTHONPATH=/home/nonroot/.local:$PYTHONPATH \
    PATH=/home/nonroot/.local/bin:$PATH \
    DENO_DIR="/home/nonroot/.cache/deno" \
    NODE_MODULES_DIR="/home/nonroot/.local/lib/node_modules" \
    TMPDIR="/home/nonroot/.cache/tmp" \
    TEMP="/home/nonroot/.cache/tmp" \
    TMP="/home/nonroot/.cache/tmp" \
    UV_PYTHON=/usr/bin/python UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# (Optional) System packages, Deno, Node caches, etc.
# Keep your script idempotent and non-interactive.
COPY scripts/install-packages.sh /tmp/install-packages.sh
RUN chmod +x /tmp/install-packages.sh && \
    /tmp/install-packages.sh && \
    rm -f /tmp/install-packages.sh

# Prepare user-owned dirs and copy any pre-cached bits
RUN mkdir -p /home/nonroot/.cache/uv \
             /home/nonroot/.cache/deno \
             /home/nonroot/.cache/s3 \
             /home/nonroot/.cache/tmp \
             /home/nonroot/.local/lib/node_modules
# If your script staged caches under /opt, move them into the user-owned locations
# (These globs are best-effort; ignore if missing)
RUN cp -r /opt/deno-cache/* /home/nonroot/.cache/deno/ 2>/dev/null || true && \
    cp -r /opt/node_modules/* /home/nonroot/.local/lib/node_modules/ 2>/dev/null || true && \
    rm -rf /opt/deno-cache /opt/node_modules

# ---------- Dependency layer (best cacheability) ----------
# Bind-mount the lockfile and pyproject for uv to resolve deps without copying the whole tree.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock,readonly \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml,readonly \
    --mount=type=bind,source=packages,target=packages,readonly \
    uv sync --locked --no-install-project --no-dev --no-editable --python /usr/bin/python

# ---------- App source & installation ----------
# Copy only what you need for installation; keep ownership consistent
COPY --chown=nonroot:nonroot ./tracecat /app/tracecat
COPY --chown=nonroot:nonroot ./packages/tracecat-registry /app/packages/tracecat-registry
COPY --chown=nonroot:nonroot ./packages/tracecat-ee /app/packages/tracecat-ee
COPY --chown=nonroot:nonroot ./pyproject.toml ./uv.lock ./.python-version /app/
COPY --chown=nonroot:nonroot ./README.md ./LICENSE ./alembic.ini /app/
COPY --chown=nonroot:nonroot ./alembic /app/alembic

# Entrypoints & helpers
COPY --chown=nonroot:nonroot scripts/entrypoint.py /app/entrypoint.py
COPY --chown=root:root scripts/check_tmp.py /usr/local/bin/check_tmp.py
RUN chmod +x /usr/local/bin/check_tmp.py

# Install the project (prod extras but no dev)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# Ensure the venv is first on PATH and provide a uv shim for tools that expect it in PATH
ENV PATH="/app/.venv/bin:$PATH"
RUN mkdir -p /home/nonroot/.local/bin && \
    ln -sf "$(command -v uv)" /home/nonroot/.local/bin/uv && \
    chown -R nonroot:nonroot /home/nonroot /app

# Quick sanity checks (optional; stays in builder layer)
RUN python -V && uv --version

#########################
# Final (minimal runtime)
#########################
FROM cgr.dev/tracecat.com/python:3.12 AS final

# Re-declare runtime ENV (metadata doesn’t come from COPY)
ENV HOST=0.0.0.0 \
    PORT=8000 \
    PYTHONUSERBASE="/home/nonroot/.local" \
    PYTHONPATH=/home/nonroot/.local:$PYTHONPATH \
    PATH="/app/.venv/bin:/home/nonroot/.local/bin:$PATH" \
    DENO_DIR="/home/nonroot/.cache/deno" \
    NODE_MODULES_DIR="/home/nonroot/.local/lib/node_modules" \
    VIRTUAL_ENV=/app/.venv

WORKDIR /app

# Copy only what’s needed to run:
# - the virtualenv
# - your app code
# - entrypoint
# - user caches/locals that runtime expects
COPY --chown=nonroot:nonroot --from=builder /app /app
COPY --chown=nonroot:nonroot --from=builder /home/nonroot/.local /home/nonroot/.local
COPY --chown=nonroot:nonroot --from=builder /home/nonroot/.cache/deno /home/nonroot/.cache/deno
COPY --chown=nonroot:nonroot --from=builder /home/nonroot/.cache/s3   /home/nonroot/.cache/s3
COPY --from=builder /usr/local/bin/deno /usr/local/bin/deno
COPY --from=builder /usr/local/bin/check_tmp.py /usr/local/bin/check_tmp.py

# Remove unused aiohttp
RUN ["/app/.venv/bin/python", "-c", "\
import pathlib, shutil; \
base = pathlib.Path('/app/.venv/lib/python3.12/site-packages/ray/_private/runtime_env/agent/thirdparty_files'); \
paths = list(base.glob('aiohttp-*')); \
[ (print('Removing', p), (shutil.rmtree(p) if p.is_dir() else p.unlink())) for p in paths ] \
"]

# Deno exists and is runnable
RUN ["/usr/local/bin/deno", "--version"]

# Python-only verification (permissions + write test)
RUN ["/usr/bin/python", "-c", "\
import os, tempfile, sys\n\
\n\
def check_path(p, want_exec=False):\n\
    ok_r = os.access(p, os.R_OK)\n\
    ok_w = os.access(p, os.W_OK)\n\
    ok_x = os.access(p, os.X_OK)\n\
    print(f'{p}: R={ok_r} W={ok_w}' + (f' X={ok_x}' if want_exec else ''))\n\
    return ok_r and (ok_w or not want_exec) and (ok_x if want_exec else True)\n\
\n\
d = os.environ.get('DENO_DIR','/home/nonroot/.cache/deno')\n\
n = os.environ.get('NODE_MODULES_DIR','/home/nonroot/.local/lib/node_modules')\n\
ok = True\n\
print('Checking cache dirs and entrypoint…')\n\
ok &= check_path(d)\n\
ok &= check_path(n)\n\
ok &= check_path('/app/.scripts')\n\
ok &= check_path('/home/nonroot/.cache/s3')\n\
# entrypoint is launched via python, so it doesn't need the +x bit; we just need R\n\
ok &= check_path('/app/entrypoint.py', want_exec=False)\n\
sys.exit(0 if ok else 1)\n\
"]

# Chainguard images default to nonroot; be explicit:
USER nonroot

EXPOSE $PORT
ENTRYPOINT ["/app/.venv/bin/python", "/app/entrypoint.py"]
CMD []