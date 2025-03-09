# Container Runner Service

This service provides isolated container execution capabilities via a hardened rootless Podman deployment.
The goal is to be able to run pre-built containers within Podman isolated from the container runner's host.

## Security Architecture Overview

### Multi-layer Defense Strategy

The security architecture implements overlapping protection mechanisms:

```
    ┌─ External Network
    │  ┌─ Network Namespace
    │  │  ┌─ User Namespace
    │  │  │  ┌─ SELinux
    │  │  │  │  ┌─ Seccomp
    ▼  ▼  ▼  ▼  ▼
┌──────────────────┐
│    Container     │
└──────────────────┘
```

### 1. Process Isolation

#### Rootless Podman with User Namespace Isolation

```
Host UID Space        Container UID Space
   │                        │
   │                    ┌───┴───┐
   │                    │UID 0  │ ─────┐
   │                    └───────┘      │
┌──┴───┐                    │      mapped to
│UID   │◄──────────────────┘          │
│100000│                               │
└──────┘                               ▼
   │                          Unprivileged User
```

* **Implementation**:
  * Podman runs as non-root user (apiuser, UID 1001)
  * User namespace remapping via `/etc/subuid` and `/etc/subgid`
  * UID/GID map range: 100000-165536 (65536 identities)
  * Container root (UID 0) maps to unprivileged host UID 100000

#### SELinux Mandatory Access Control

* **Implementation**: Type enforcement with standard container context
* **Technical details**:
  * Using container-selinux and selinux-policy-targeted packages
  * Process type: `container_t`
  * File type: `container_file_t`
  * SELinux contexts applied automatically by Podman

### 2. Filesystem Isolation

#### Storage Containment

* **Implementation**: Container storage isolated in user's home directory
* **Technical details**:
  * Default storage location for rootless Podman
  * Mount options enforced through containers.conf:
    ```
    nodev:   Prevents device file creation
    nosuid:  Ignores SUID/SGID bits
    noexec:  Prevents executable files
    ```
  * Overlay filesystem with SELinux awareness via fuse-overlayfs

#### Seccomp Syscall Filtering

* **Implementation**: Default-deny policy with minimal allowlist
* **Technical details**:
  * Default action: `SCMP_ACT_ERRNO` (deny all by default)
  * Architecture-specific: x86_64
  * Minimal syscall allowlist:
    * Basic I/O: read, write, open, close
    * Process: exit, exit_group, futex
    * Network: socket, connect, getpeername, getsockname, setsockopt, recvfrom, sendto
    * Event handling: epoll_ctl, epoll_wait, poll, fcntl

### 3. Network Isolation

#### Netavark Containment

* **Implementation**: Custom isolated bridge networks
* **Technical details**:
  * Network backend: Netavark (native Podman implementation)
  * Custom subnet allocation: `10.89.0.0/16` and `10.90.0.0/15`
  * Network isolation parameter: `isolate = true`
  * DNS servers: `1.1.1.1`, `8.8.8.8` (trusted upstreams)

### 4. Resource Isolation

#### Resource Limits

* **Implementation**: Strict resource quotas via cgroups
* **Technical details**:
  * Memory limit: 512MB with equivalent swap limit
  * CPU quota: 20% of available CPU (20000/100000)
  * Process limit: 100 processes per container

#### Capability Restrictions

* **Implementation**: Empty capability set by default
* **Technical details**:
  * `default_capabilities = []` in containers.conf
  * No privilege escalation: `no_new_privileges = true`
  * No sysctl modifications: `default_sysctls = []`

## Configuration

### Environment Variables

- `PODMAN_API_VERSION`: API version for client compatibility (default: `v1.40`)
- `PODMAN_LISTEN_ADDRESS`: Service bind address (default: `127.0.0.1`)

## Usage

The executor service connects to the container-runner service. The container-runner service exposes its API on port 8080.

### Example (Python Client)

```python
from tracecat.sandbox.podman import run_podman_container, PodmanNetwork

result = run_podman_container(
    image="alpine:latest",
    command=["echo", "Hello, World!"],
    network=PodmanNetwork.NONE  # Complete network isolation
)
print(result.output)
```

## Security Verification

### Key Verification Commands

```bash
# Check if Podman is running as non-root
docker exec -it container-runner ps -ef | grep podman

# Verify user namespace configuration
docker exec -it container-runner grep apiuser /etc/subuid

# Check SELinux is enforcing
docker exec -it container-runner getenforce

# Verify seccomp profile is in place
docker exec -it container-runner ls -l /etc/containers/seccomp.json
```
