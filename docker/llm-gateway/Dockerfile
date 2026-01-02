# Lightweight LiteLLM Gateway
# Uses official LiteLLM image with custom auth module

FROM ghcr.io/berriai/litellm:main-latest

WORKDIR /app

# Install uv for fast package management
COPY --from=ghcr.io/astral-sh/uv:0.9.15 /uv /usr/local/bin/uv

# Install additional dependencies for custom auth
RUN uv pip install --system --no-cache \
    pyjwt \
    "sqlalchemy[asyncio]" \
    asyncpg \
    "psycopg[binary]" \
    cryptography \
    orjson

# Copy gateway files
COPY custom_auth.py /app/custom_auth.py
COPY litellm_config.yaml /app/litellm_config.yaml

ENV PYTHONPATH=/app

EXPOSE 4000

# Override the base image's entrypoint which expects docker/prod_entrypoint.sh
ENTRYPOINT ["litellm"]
CMD ["--config", "/app/litellm_config.yaml", "--port", "4000"]
