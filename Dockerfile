FROM python:3.12-slim-bookworm

ENV API_MODULE=tracecat.api:app
ENV HOST=0.0.0.0
ENV PORT=8000

EXPOSE $PORT

COPY scripts/install-packages.sh .
RUN chmod +x install-packages.sh && \
    ./install-packages.sh && \
    chmod -x install-packages.sh && \
    rm install-packages.sh

COPY scripts/auto-update.sh ./auto-update.sh
RUN chmod +x auto-update.sh && \
    ./auto-update.sh && \
    chmod -x auto-update.sh && \
    rm auto-update.sh

RUN useradd --create-home apiuser
WORKDIR /app
USER apiuser

COPY --chown=apiuser:apiuser ./tracecat /app/tracecat
COPY --chown=apiuser:apiuser ./pyproject.toml /app/pyproject.toml
COPY --chown=apiuser:apiuser ./requirements.txt /app/requirements.txt
COPY --chown=apiuser:apiuser ./README.md /app/README.md
COPY --chown=apiuser:apiuser ./LICENSE /app/LICENSE

RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt

CMD ["sh", "-c", "python3 -m uvicorn $API_MODULE --host $HOST --port $PORT"]
