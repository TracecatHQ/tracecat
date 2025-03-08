FROM quay.io/containers/podman:v5.4.0

# Copy the setup script and configuration files
COPY setup-podman.sh /tmp/setup-podman.sh
COPY containers.conf /app/config/podman/containers.conf
COPY storage.conf /app/config/podman/storage.conf
COPY seccomp.json /app/config/podman/seccomp.json

# Run the setup script to configure Podman with security hardening
RUN chmod +x /tmp/setup-podman.sh && \
    /tmp/setup-podman.sh && \
    rm /tmp/setup-podman.sh

# Create home directories for the apiuser
RUN mkdir -p /home/apiuser/.cache /home/apiuser/.local && \
    chown -R apiuser:apiuser /home/apiuser/.cache /home/apiuser/.local && \
    chmod -R 755 /home/apiuser/.cache /home/apiuser/.local && \
    chown -R apiuser:apiuser /tmp /home/apiuser

# Set environment variables
ENV HOME="/home/apiuser"
ENV PODMAN_API_VERSION=v1.40
ENV PODMAN_LISTEN_ADDRESS=127.0.0.1

# Expose the podman API port
EXPOSE 8080

# Note: We need to run as root because podman system service requires it
# However, containers launched by this podman instance will run as non-root
# using the configured user namespace remapping

# Command to run the podman API service with secure defaults
CMD ["podman", "system", "service", "tcp:127.0.0.1:8080", "--time=0"]
