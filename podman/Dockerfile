FROM quay.io/containers/podman:v5.4.0

# Install required packages and clean up in one layer
RUN dnf install -y \
    container-selinux \
    selinux-policy-targeted \
    fuse-overlayfs \
    && dnf clean all

# Create non-root user for running podman
RUN useradd -m -u 1001 podman-user && \
    mkdir -p /etc/containers && \
    chown -R podman-user:podman-user /etc/containers

# Copy configs with correct ownership
COPY --chown=podman-user:podman-user containers.conf /etc/containers/containers.conf
COPY --chown=podman-user:podman-user storage.conf /etc/containers/storage.conf
COPY --chown=podman-user:podman-user seccomp.json /etc/containers/seccomp.json

# Set up user namespace mapping
RUN echo "podman-user:100000:65536" > /etc/subuid && \
    echo "podman-user:100000:65536" > /etc/subgid && \
    chmod 644 /etc/sub{u,g}id

# Create the containers directory
# Grant ownership to the podman-user
RUN mkdir -p /var/lib/containers && \
    chown -R podman-user:podman-user /var/lib/containers

# Switch to non-root user
USER podman-user

EXPOSE $PORT

# Run podman API service as non-root
CMD ["podman", "system", "service", "--time=0", "--log-level=debug", "tcp://0.0.0.0:8080"]
