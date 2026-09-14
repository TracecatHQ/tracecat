# syntax=docker/dockerfile:1
# BASE_IMAGE must be the deployed image's immutable registry digest.
ARG BASE_IMAGE
FROM ${BASE_IMAGE} AS builder
USER root

RUN test -f /etc/debian_version && test "$PG_MAJOR" = 16 \
    && test "$(pg_config --pkglibdir)" = /usr/lib/postgresql/16/lib \
    && test ! -e /usr/lib/postgresql/16/lib/vector.so
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential ca-certificates git postgresql-server-dev-16
RUN git clone https://github.com/pgvector/pgvector.git /tmp/pgvector \
    && cd /tmp/pgvector && git checkout 8ee86c96f0fd72390f890aa8a336fda6d3ab4c6c \
    && make OPTFLAGS="" with_llvm=no \
    && make install DESTDIR=/out with_llvm=no \
    && install -D -m 644 LICENSE /out/usr/share/doc/pgvector/LICENSE

# Only extension files cross this boundary. No package installation or upgrade
# runs in the final image; PostgreSQL, libc, ICU and entrypoint remain inherited.
FROM ${BASE_IMAGE}
COPY --from=builder /out/ /
