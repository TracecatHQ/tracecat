FROM quay.io/containers/podman:v5.4.0

# Install additional dependencies
RUN dnf install -y \
    crun \
    uidmap \
    && dnf clean all

# Create necessary directories
RUN mkdir -p /etc/containers \
    && mkdir -p /etc/subuid /etc/subgid \
    && mkdir -p /run/podman \
    && mkdir -p /run/containers/storage \
    && mkdir -p /var/lib/containers/storage \
    && mkdir -p /var/lib/containers/storage/volumes

# Set up user namespaces for rootless mode
RUN echo "podman:100000:65536" >> /etc/subuid \
    && echo "podman:100000:65536" >> /etc/subgid

# Set secure permissions
RUN chmod 750 /run/podman \
    && chmod -R 770 /var/lib/containers/storage \
    && chmod -R 770 /run/containers/storage

# Set ownership for storage directories
RUN chown -R root:podman /var/lib/containers/storage \
    && chown -R root:podman /run/containers/storage \
    && chown root:podman /run/podman

# Expose the podman API port
EXPOSE 8080

# Set default environment variables
ENV PODMAN_API_VERSION=v1.40
# Listen on all interfaces to allow inter-container communication
ENV PODMAN_LISTEN_ADDRESS=0.0.0.0

# Command to run the podman API service
# --time=0: Run indefinitely (no timeout)
# tcp:$PODMAN_LISTEN_ADDRESS:8080: Listen on the specified address and port
CMD podman system service tcp:${PODMAN_LISTEN_ADDRESS}:8080 --time=0
