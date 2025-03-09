# Container Runner Service

A hardened rootless Podman deployment for executing untrusted containers in an isolated environment.

## Security Model: Multi-Layer Isolation

The service implements multiple isolation mechanisms functioning as independent security boundaries:

```
┌─ Network Restrictions
│  ┌─ Resource Constraints
│  │  ┌─ Capability Controls
│  │  │  ┌─ User Namespace Isolation
▼  ▼  ▼  ▼  ▼
┌──────────────────┐
│    Container     │
└──────────────────┘
```

## Core Security Controls

### 1. Rootless Execution

**Podman runs as unprivileged user:**
- Non-root user `apiuser` (UID 1001)
- User namespace remapping via `/etc/subuid` and `/etc/subgid`
- UID/GID range: 100000-165536
- Root in container (UID 0) maps to unprivileged host UID 100000

### 2. System Call Filtering (Seccomp)

**Default-deny whitelist approach:**
- Default action: `SCMP_ACT_ERRNO` (deny all)
- Architecture-specific: x86_64
- Minimal syscall allowlist:
  ```
  read, write, open, close, exit, exit_group, socket, connect,
  getpeername, getsockname, setsockopt, recvfrom, sendto, futex,
  epoll_ctl, epoll_wait, poll, fcntl
  ```

### 3. SELinux Mandatory Access Control

**Configuration Files:**
```ini
# containers.conf - Container runtime configuration
[containers]
# Container process labeling
label = "system_u:system_r:container_t:s0"
label_opts = ["disable=false"]
label_range = "c0,c1023"

# storage.conf - Storage and mount configuration
[storage]
# Runtime process labeling for storage operations
selinux_process_label = "system_u:system_r:container_runtime_t:s0"
# Filesystem mount labeling
selinux_mount_context = "system_u:object_r:container_file_t:s0"
mount_program_options = ["--selinuxcontext"]
```

**Process and File Context Implementation:**

1. **Process Domain (`system_u:system_r:container_t:s0`):**
   - Source: `containers.conf` → `label`
   - Applied to all processes inside containers
   - Enforced by SELinux policy installed via `container-selinux` package
   - Process transitions controlled by `label_opts = ["disable=false"]`
   - MCS categories dynamically assigned from range `c0-c1023`

2. **Runtime Context (`system_u:system_r:container_runtime_t:s0`):**
   - Source: `storage.conf` → `selinux_process_label`
   - Applied to Podman API service running as `apiuser` (UID 1001)
   - Controls access to:
     - `/etc/containers/*` configuration files
     - Container storage root at `/var/lib/containers/storage`
     - Runtime directory at `/run/containers/storage`

3. **Storage Implementation:**
   - Source: `storage.conf` → `mount_program`, `mountopt`
   - SELinux-aware overlay via `fuse-overlayfs` mount program
   - Mount options: `nodev,metacopy=on,overlay.mount_program=/usr/bin/fuse-overlayfs`
   - Context persistence enforced by `mount_program_options = ["--selinuxcontext"]`
   - Storage paths labeled with `system_u:object_r:container_file_t:s0`
   - Overlay operations maintain xattr-based SELinux labels across layers

4. **Container Isolation:**
   - Source: `containers.conf` → `label_range`
   - Container process domain: `container_t`
   - Container file access: `container_file_t`
   - Runtime management: `container_runtime_t`
   - Cross-container isolation:
     - MCS category range defined in containers.conf: `label_range = "c0,c1023"`
     - Unique category pairs assigned per container
     - Mandatory access control enforced by kernel LSM
     - Prevents unauthorized file/process access between containers

All contexts and policies installed via `selinux-policy-targeted` and `container-selinux` packages in Dockerfile.

**Quick Verification:**
```bash
# Check process contexts
ps -eZ | grep container

# Verify storage labels
ls -Z /var/lib/containers/storage/

# Monitor SELinux denials
ausearch -m AVC -ts recent
```

### 4. Capability Restrictions

**Empty capability set by default:**
- `default_capabilities = []` in containers.conf
- No privilege escalation: `no_new_privileges = true`
- No sysctl modifications: `default_sysctls = []`

### 5. Filesystem Protections

**Secure mount options enforced:**
- `nodev`: Prevents device file creation
- `nosuid`: Ignores SUID/SGID bits
- `noexec`: Prevents executable files
- Isolated overlay storage with SELinux awareness

### 6. Network Isolation

**Controlled network access:**
- Network backend: Netavark with isolated bridge networks
- Subnet allocation: `10.89.0.0/16` and `10.90.0.0/15`
- Container-to-container isolation: `isolate = true`
- Restricted DNS: `1.1.1.1`, `8.8.8.8` (trusted upstream resolvers)

### 7. Resource Limits

**Strict resource quotas via cgroups:**
- Memory: 512MB limit (incl. swap)
- CPU: 20% quota (20000/100000)
- Process limit: 100 processes per container
- Volume cleanup to prevent data leaks

## API and Usage

The service exposes a Podman API on port 8080 (bound to 127.0.0.1 by default):

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

```bash
# Verify rootless execution
ps -ef | grep podman  # Should run as apiuser

# Confirm user namespace mapping
grep apiuser /etc/subuid

# Verify SELinux enforcement
getenforce  # Should return "Enforcing"

# Check seccomp profile
ls -l /etc/containers/seccomp.json
```

## Implementation Details

Configuration spread across multiple files:
- `Dockerfile`: Base setup, user creation, package installation
- `seccomp.json`: System call filtering policy
- `containers.conf`: Runtime behavior, capabilities, resource limits
- `storage.conf`: Filesystem isolation, SELinux contexts, mount options
