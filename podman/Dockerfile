FROM quay.io/containers/podman:v5.4.0

# Install required packages and clean up in one layer
RUN dnf install -y \
    container-selinux \
    selinux-policy-targeted \
    fuse-overlayfs \
    && dnf clean all

# Create non-root user for running podman
RUN useradd -m -u 1001 apiuser && \
    mkdir -p /etc/containers && \
    chown -R apiuser:apiuser /etc/containers

# Copy configs with correct ownership
COPY --chown=apiuser:apiuser containers.conf /etc/containers/containers.conf
COPY --chown=apiuser:apiuser storage.conf /etc/containers/storage.conf
COPY --chown=apiuser:apiuser seccomp.json /etc/containers/seccomp.json

# Set up user namespace mapping
RUN echo "apiuser:100000:65536" > /etc/subuid && \
    echo "apiuser:100000:65536" > /etc/subgid && \
    chmod 644 /etc/sub{u,g}id

# Switch to non-root user
USER apiuser

EXPOSE 8080

# Run podman API service as non-root
CMD ["podman", "system", "service", "tcp:127.0.0.1:8080", "--time=0"]
