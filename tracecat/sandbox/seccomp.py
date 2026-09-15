"""Shared seccomp policies for Tracecat nsjail sandboxes."""

from __future__ import annotations

_UNTRUSTED_BLOCKED_SYSCALLS: tuple[str, ...] = (
    "add_key",
    "bpf",
    "clock_adjtime",
    "clock_settime",
    "delete_module",
    "fanotify_init",
    "finit_module",
    "fsconfig",
    "fsmount",
    "fsopen",
    "init_module",
    "io_uring_enter",
    "io_uring_register",
    "io_uring_setup",
    "kcmp",
    "kexec_file_load",
    "kexec_load",
    "keyctl",
    "lookup_dcookie",
    "mount",
    "mount_setattr",
    "move_mount",
    "name_to_handle_at",
    "open_by_handle_at",
    "open_tree",
    "perf_event_open",
    "pivot_root",
    "process_vm_readv",
    "process_vm_writev",
    "ptrace",
    "quotactl",
    "reboot",
    "request_key",
    "setns",
    "settimeofday",
    "swapoff",
    "swapon",
    "syslog",
    "umount",
    "unshare",
    "userfaultfd",
)


def build_untrusted_seccomp_policy() -> str:
    """Build a conservative seccomp policy for untrusted Python sandboxes.

    The policy intentionally starts with a denylist rather than a full allowlist
    so Tracecat can harden existing workloads without breaking common Python,
    subprocess, and networking behavior on the first rollout. It blocks
    tracing, cross-process memory inspection, mount and namespace mutation,
    keyring access, module loading, io_uring (a recurring kernel exploit
    surface that sandboxed workloads do not need), and other kernel-facing
    syscalls that are not required once nsjail has already created the sandbox.

    Note: ``clone`` is intentionally not blanket-blocked because Python
    threading and subprocess spawning require it; this Kafel build cannot
    arg-filter clone flags (verified empirically), so nested user-namespace
    creation remains reachable and is mitigated at the container level
    (cgroup pids limit) instead.

    Returns:
        A Kafel policy string suitable for nsjail's ``seccomp_string``
        configuration field. Rules are comma-separated — the bundled Kafel
        enforces a single errno value per policy, so clone3 gets its own
        ERRNO(38) rule (verified empirically).
    """
    blocked_syscalls = ", ".join(_UNTRUSTED_BLOCKED_SYSCALLS)
    return (
        "POLICY tracecat_untrusted { "
        f"ERRNO(1) {{ {blocked_syscalls} }}, "
        # clone3 with ENOSYS (not EPERM) so glibc's clone3→clone fallback
        # triggers cleanly — matches Docker's default-profile semantics.
        # Verified live: threads, fork, and uvloop all work under this rule.
        "ERRNO(38) { clone3 } "
        "} USE tracecat_untrusted DEFAULT ALLOW"
    )
