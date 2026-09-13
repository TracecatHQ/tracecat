#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../.."

for tool in uv node pnpm; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "Missing $tool. Install uv, Node.js 22, and pnpm before running setup." >&2
    exit 1
  fi
done

if [[ "$(node -p 'process.versions.node.split(".")[0]')" != "22" ]]; then
  echo "Tracecat requires Node.js 22. Activate it, then rerun setup." >&2
  exit 1
fi

# Use this checkout's Python version and lockfile, including test/lint and admin
# tools. Cloning avoids making the virtualenv depend on prunable uv cache symlinks.
uv sync --locked --python "$(cat .python-version)" --group dev --group admin --link-mode clone
pnpm -C frontend install --frozen-lockfile

echo "Tracecat dependencies are ready."
echo "Use the Start cluster action when you need local services."
echo "The cluster command creates development .env settings if they are missing."
