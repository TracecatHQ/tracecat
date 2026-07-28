#!/usr/bin/env bash
set -euo pipefail

# Agent-executor entrypoint for deployments that enable per-sandbox cgroup
# memory limits. Start the container as root with this entrypoint (compose:
# user "0:0"; Kubernetes: runAsUser 0) and it hands the container's own
# cgroup v2 directory to apiuser so the worker can prepare nsjail child
# cgroups without root, then drops privileges before starting it. The worker
# rejects startup if cgroup enforcement is enabled but delegation failed.
# Non-root invocations pass straight through to that same fail-closed check.
if [[ "$(id -u)" == "0" ]]; then
    # Mirror config.env_bool falsy values so disabling the feature also skips
    # the root-side delegation, not just the Python-side preparation. The
    # privilege drop below is unconditional: root never reaches the worker.
    parse_bool_env() {
        local name="$1" default="$2" value
        value="$(
            printf '%s' "${!name:-$default}" |
                tr '[:upper:]' '[:lower:]' |
                sed 's/^[[:space:]]*//;s/[[:space:]]*$//'
        )"
        case "$value" in
            1 | true | yes | on) echo true ;;
            0 | false | no | off) echo false ;;
            *)
                echo "$name must be a boolean value" >&2
                exit 64
                ;;
        esac
    }
    cgroup_enabled="$(parse_bool_env TRACECAT__AGENT_SANDBOX_CGROUP_ENABLED true)"
    nsjail_disabled="$(parse_bool_env TRACECAT__DISABLE_NSJAIL true)"

    # The worker only uses the delegated cgroup when nsjail sandboxing is on
    # (executor_worker.py computes cgroup_required the same way), so skip the
    # remount + chown entirely when nsjail is disabled.
    if [[ "$cgroup_enabled" == "true" && "$nsjail_disabled" == "false" ]]; then
        # Resolve this container's cgroup v2 directory: the mount root under a
        # private cgroup namespace, a subpath of the host cgroupfs otherwise
        # (e.g. privileged Kubernetes). Never touch anything above it, and
        # never touch anything at all without a unified v2 entry — on a
        # cgroup v1 host an empty match would otherwise point at the
        # cgroupfs root.
        cgroup_path="$(sed -n 's/^0:://p' /proc/self/cgroup | head -n 1)"
        if [[ -z "$cgroup_path" ]]; then
            echo "No cgroup v2 entry in /proc/self/cgroup; agent sandbox" \
                "cgroup limits cannot be enforced." >&2
        else
            cgroup_rel="${cgroup_path#/}"
            cgroup_dir="/sys/fs/cgroup${cgroup_rel:+/$cgroup_rel}"
            mount -o remount,rw /sys/fs/cgroup 2>/dev/null || true
            if [[ -f "$cgroup_dir/cgroup.controllers" ]] &&
                chown apiuser:apiuser "$cgroup_dir" "$cgroup_dir/cgroup.procs" \
                    "$cgroup_dir/cgroup.subtree_control" "$cgroup_dir/cgroup.threads"; then
                echo "Delegated $cgroup_dir to apiuser."
            else
                echo "Unable to delegate $cgroup_dir to apiuser; agent sandbox" \
                    "cgroup limits cannot be enforced." >&2
            fi
        fi
    fi
    # setpriv changes only IDs; fix the identity env vars ourselves instead of
    # --reset-env, which would clear the service configuration environment.
    export HOME=/home/apiuser USER=apiuser LOGNAME=apiuser
    exec setpriv --reuid=apiuser --regid=apiuser --init-groups "$@"
fi

exec "$@"
