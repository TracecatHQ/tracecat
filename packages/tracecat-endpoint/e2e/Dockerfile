FROM ubuntu:24.04

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --home-dir /home/tracecat --shell /bin/bash tracecat \
    && mkdir -p /state \
    && chown -R tracecat:tracecat /home/tracecat /state

COPY dist/tracecatd /usr/local/bin/tracecatd
COPY e2e/device-entrypoint.sh /usr/local/bin/device-entrypoint.sh

RUN chmod 0755 /usr/local/bin/tracecatd /usr/local/bin/device-entrypoint.sh

USER tracecat
WORKDIR /home/tracecat

ENTRYPOINT ["/usr/local/bin/device-entrypoint.sh"]
