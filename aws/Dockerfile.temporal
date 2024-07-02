FROM alpine:3.20.1

WORKDIR /usr/src/app

RUN apk add --no-cache \
    curl=8.8.0-r0 \
    tar=1.35-r2 \
    && addgroup -S tgroup -g 10001 \
    && adduser -S tuser -u 10000 -G tgroup

# Switch to root temporarily to change directory ownership
USER root
RUN mkdir -p /usr/src/app \
    && chown -R tuser:tgroup /usr/src/app
USER tuser:tgroup

# Download and extract Temporal CLI
RUN curl -L -o temporal.tar.gz "https://temporal.download/cli/archive/latest?platform=linux&arch=amd64" \
    && mkdir -p temporal-cli \
    && tar -xzf temporal.tar.gz -C temporal-cli \
    && mv temporal-cli/temporal /usr/src/app/temporal \
    && rm -rf temporal.tar.gz temporal-cli

EXPOSE 7233 8080

CMD ["./temporal", "server", "start-dev", "--ui-port", "8080", "--db-filename", "temporal.db"]
