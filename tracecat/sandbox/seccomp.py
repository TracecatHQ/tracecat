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
    "umount2",
    "unshare",
    "userfaultfd",
)


def build_untrusted_seccomp_policy() -> str:
    """Build a conservative seccomp policy for untrusted Python sandboxes.

    The policy intentionally starts with a denylist rather than a full allowlist
    so Tracecat can harden existing workloads without breaking common Python,
    subprocess, and networking behavior on the first rollout. It blocks
    tracing, cross-process memory inspection, mount and namespace mutation,
    keyring access, module loading, and other kernel-facing syscalls that are
    not required once nsjail has already created the sandbox.

    Returns:
        A single-line Kafel policy string suitable for nsjail's
        ``seccomp_string`` configuration field.
    """
    blocked_syscalls = ", ".join(_UNTRUSTED_BLOCKED_SYSCALLS)
    return (
        "POLICY tracecat_untrusted { "
        f"ERRNO(1) {{ {blocked_syscalls} }} "
        "} USE tracecat_untrusted DEFAULT ALLOW"
    )
