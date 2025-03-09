# Container Runner Service: Security Architecture

This document outlines the comprehensive security architecture of the container-runner service, which provides isolated container execution capabilities via a sandboxed Podman API.

## Architecture Overview

The container-runner implements a multi-layered isolation model following defense-in-depth principles:

```
┌──────────────────────────────────────────────────────────────┐
│ Docker Container (Host)                                      │
│ ┌────────────────────────────────────────────────────────┐   │
│ │ Podman Service Container                               │   │
│ │                                                        │   │
│ │ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐    │   │
│ │ │ Container 1  │ │ Container 2  │ │ Container N  │    │   │
│ │ │ (isolated)   │ │ (isolated)   │ │ (isolated)   │    │   │
│ │ └──────────────┘ └──────────────┘ └──────────────┘    │   │
│ │                                                        │   │
│ │ SELinux + User Namespace + Network + Resource Controls │   │
│ └────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────┘
```

## Isolation Domains

### 1. Process Isolation

#### User Namespace Isolation

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

* **Implementation**: User namespace remapping through `/etc/subuid` and `/etc/subgid`
* **Technical details**:
  * UID/GID map range: 100000-165536 (65536 identities)
  * Root inside container (UID 0) maps to unprivileged host UID 100000
  * Enforced via `userns_mode = "auto"` in containers.conf
  * Persisted through `/etc/subuid` entries: `apiuser:100000:65536`
* **Attack surface reduction**:
  * Even with root access in container, process runs as unprivileged on host
  * Container root cannot access host resources outside mapped range
  * File capabilities limited to container namespace

#### SELinux Mandatory Access Control

* **Implementation**: Type enforcement with Multi-Category Security (MCS)
* **Technical details**:
  * Process type: `container_t`
  * File type: `container_file_t`
  * MCS categories: `s0:c1,c2` (unique per container)
  * SELinux contexts: `system_u:system_r:container_t:s0:c1,c2`
* **Attack vectors blocked**:
  * Process context transitions controlled by SELinux policy
  * Even container root cannot access files with different types
  * Container processes isolated from each other via MCS categories
  * Cannot modify SELinux contexts even with privileged access

#### SELinux Security Model Deep Dive

The container-runner service implements a comprehensive SELinux security model with three core protection layers:

```ascii
┌─────────────────────────────────────────────────────────┐
│ SELinux Security Model                                  │
│                                                         │
│  ┌─────────────────┐      ┌──────────────────┐         │
│  │ Type Enforcement│      │ MCS Categories    │         │
│  │                 │      │                   │         │
│  │ container_t     │──────│ s0:c1,c2         │         │
│  │ container_file_t│      │ (unique per      │         │
│  │                 │      │  container)       │         │
│  └─────────────────┘      └──────────────────┘         │
│                                                         │
│  ┌──────────────────────────────────────────┐          │
│  │           Access Controls                 │          │
│  │                                          │          │
│  │ - Process boundaries                     │          │
│  │ - File access                           │          │
│  │ - Network isolation                      │          │
│  └──────────────────────────────────────────┘          │
└─────────────────────────────────────────────────────────┘
```

**1. Type Enforcement (TE)**
- Defines what processes can do and what files they can access
- Container processes run as `container_t`
- Container files are labeled as `container_file_t`
- Prevents containers from accessing host system resources

**2. Multi-Category Security (MCS)**
```ascii
Container A                     Container B
┌──────────────┐               ┌──────────────┐
│ s0:c1,c2     │   ≠   Access │ s0:c3,c4     │
│ (Category 1) │ ◄─╳──────────│ (Category 2) │
└──────────────┘               └──────────────┘
```
- Each container gets unique security categories
- Prevents containers from accessing each other's resources
- Automatic isolation between workloads

**3. Access Controls**
- **Process**: Prevents privilege escalation and controls process transitions
- **Filesystem**: Automatically labels mounted volumes and enforces access rules
- **Network**: Labels and controls network traffic between containers

This multi-layered approach ensures:
- Containers cannot break out of their assigned boundaries
- Host system resources are protected
- Containers are isolated from each other
- Mounted volumes maintain proper security context
- Network traffic is controlled and isolated

### 2. Filesystem Isolation

#### Storage Containment

* **Implementation**: Controlled mount namespaces with SELinux labeling
* **Technical details**:
  * Storage confined to `/var/lib/containers/storage` and `/run/containers/storage`
  * Overlay filesystem with SELinux awareness via fuse-overlayfs
  * Mount options: `nodev,nosuid,noexec`
  * SELinux mount context: `system_u:object_r:container_file_t:s0`
* **Security boundaries**:
  * Double isolation via filesystem namespaces and SELinux types
  * Host filesystem invisible to container processes
  * Volume mounts restricted with security options
  * Container volumes receive proper SELinux context

#### Seccomp Syscall Filtering

* **Implementation**: Default-deny policy with explicit allowlist
* **Technical details**:
  * Default action: `SCMP_ACT_ERRNO` (deny all by default)
  * Architecture-specific: x86_64
  * Limited syscall set: basic I/O, networking, inter-process communication
  * No dangerous syscalls: no `ptrace`, `mount`, kernel module ops, etc.
* **Vulnerability mitigation**:
  * Prevents container breakout via syscall exploitation
  * Blocks access to sensitive kernel functionality
  * Reduces kernel attack surface to minimum required set
  * Complements namespace isolation at syscall boundary

### 3. Network Isolation

#### Netavark Containment

* **Implementation**: Custom isolated bridge networks
* **Technical details**:
  * Network backend: Netavark (native Podman implementation)
  * Custom subnet allocation: `10.89.0.0/16` and `10.90.0.0/15`
  * Network isolation parameter: `isolate = true`
  * DNS servers: `1.1.1.1`, `8.8.8.8` (trusted upstreams)
* **Security properties**:
  * Container-to-container traffic blocked between different networks
  * Predictable address allocation in non-standard ranges
  * Independent DNS resolution preventing poisoning attacks
  * No direct access to host network namespace

#### API Endpoint Security

* **Implementation**: Localhost-bound service with controlled exposure
* **Technical details**:
  * Podman API service listens on `127.0.0.1:8080`
  * TCP encapsulation for API calls
  * Accessed via Docker network from specific containers only
* **Attack surface reduction**:
  * API only accessible within Docker network
  * No external exposure of Podman API
  * IP-based access restriction

### 4. Resource Isolation

#### cgroups Containment

* **Implementation**: Strict resource quotas via cgroups
* **Technical details**:
  * Memory limit: 512MB with equivalent swap limit
  * CPU quota: 50% of available CPU (50000/100000)
  * Process limit: 100 processes per container
  * cgroups manager: cgroupfs
* **Security impact**:
  * Prevents resource starvation attacks
  * Limits impact of fork bombs
  * Prevents memory-based DoS attacks
  * Ensures fair resource allocation

#### Capability Restrictions

* **Implementation**: Empty capability set by default
* **Technical details**:
  * `default_capabilities = []` in containers.conf
  * No privilege escalation: `no_new_privileges = true`
  * No sysctl modifications: `default_sysctls = []`
* **Protection provided**:
  * Containers cannot perform privileged operations
  * Cannot modify system-wide kernel parameters
  * No ability to load kernel modules or manipulate hardware

## Defense in Depth Strategy

The security architecture implements overlapping protection mechanisms to ensure that compromise of one layer doesn't lead to full system compromise:

| Attack Vector | Primary Defense | Secondary Defense | Tertiary Defense |
|---------------|----------------|-------------------|------------------|
| Container Escape | User Namespace Isolation | SELinux Enforcement | Seccomp Filtering |
| Privilege Escalation | No Capabilities | No New Privileges | SELinux Type Enforcement |
| Resource Attacks | cgroups Limits | Process Limits | CPU/Memory Quotas |
| Filesystem Access | Mount Namespace | SELinux Labels | Mount Options |
| Network Attacks | Network Namespace | Netavark Isolation | Limited Exposure |

The security model maintains these characteristics even if individual protections fail:

1. **Container-to-host isolation**: Protected by user namespaces + SELinux + seccomp
2. **Container-to-container isolation**: Protected by SELinux MCS + network isolation
3. **Resource protection**: Protected by cgroups + process limits
4. **Filesystem protection**: Protected by SELinux + mount options + overlay isolation

## Configuration

### Environment Variables

- `TRACECAT__PODMAN_URI`: API endpoint URI (default: `tcp://container-runner:8080`)
- `PODMAN_API_VERSION`: API version for client compatibility (default: `v1.40`)
- `PODMAN_LISTEN_ADDRESS`: Service bind address (default: `127.0.0.1`)

## Usage

The executor service connects to the container-runner service using the `TRACECAT__PODMAN_URI` environment variable. The container-runner service exposes its API on port 8080.

### Example

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

### SELinux Verification

```bash
# Verify SELinux is enforcing
docker exec -it container-runner getenforce
# Expected: Enforcing

# Verify container processes have correct context
docker exec -it container-runner podman exec <container_id> ps -efZ
# Should show: system_u:system_r:container_t:s0:c1,c2

# Check for SELinux denials
docker exec -it container-runner ausearch -m avc -ts recent
```

### Namespace Verification

```bash
# Verify user namespaces are in use
docker exec -it container-runner podman info | grep userns
# Should show: userns: true

# Check UID mapping
docker exec -it container-runner podman inspect <container_id> | grep -A 10 IDMappings
```

### Network Isolation Testing

```bash
# Create two containers on default network
docker exec -it container-runner podman run -d --name c1 alpine sleep 1000
docker exec -it container-runner podman run -d --name c2 alpine sleep 1000

# Verify isolation (should fail to connect)
docker exec -it container-runner podman exec c1 ping -c 1 $(podman inspect -f '{{.NetworkSettings.IPAddress}}' c2)
```

### Resource Limit Verification

```bash
# Verify memory limits
docker exec -it container-runner podman exec <container_id> cat /sys/fs/cgroup/memory/memory.limit_in_bytes
# Should show: 536870912 (512MB)

# Verify process limits
docker exec -it container-runner podman exec <container_id> cat /sys/fs/cgroup/pids/pids.max
# Should show: 100
```

## Troubleshooting

### SELinux Issues

```bash
# Temporarily set to permissive for debugging
docker exec -it container-runner setenforce 0

# Get detailed SELinux denials with context
docker exec -it container-runner ausearch -m avc -ts recent | audit2why
```

### Network Connectivity Issues

```bash
# Check DNS resolution
docker exec -it container-runner podman run --rm alpine nslookup google.com

# Inspect network configuration
docker exec -it container-runner podman inspect --format '{{.NetworkSettings}}' <container_id>
```

### API Access Issues

```bash
# Verify API is responsive
docker exec -it container-runner curl http://127.0.0.1:8080/v1.40/version

# Check API service logs
docker exec -it container-runner journalctl -u podman
```
