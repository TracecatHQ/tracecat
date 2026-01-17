#!/bin/bash

set -e

echo "🚀 Installing Claude Code secure container..."

# Check if Docker is installed
if ! command -v docker &>/dev/null; then
  echo "❌ Docker not found. Install Docker Desktop first: https://docker.com/products/docker-desktop"
  exit 1
fi

# Check if Docker is running
if ! docker info &>/dev/null; then
  echo "❌ Docker isn't running. Start Docker Desktop and try again."
  exit 1
fi

echo "✅ Docker is ready"

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Build the secure container
echo "🔨 Building secure Claude container (this might take a few minutes)..."
docker build -t claude-secure -f "$SCRIPT_DIR/claude.Dockerfile" "$SCRIPT_DIR"

echo "🛡️ Container built with security firewall"

# Detect shell config file
SHELL_CONFIG=""
if [[ "$SHELL" == *"zsh"* ]]; then
  SHELL_CONFIG="$HOME/.zshrc"
elif [[ "$SHELL" == *"bash"* ]]; then
  SHELL_CONFIG="$HOME/.bashrc"
else
  SHELL_CONFIG="$HOME/.profile"
fi

# Remove existing yolo alias blocks before adding new one
sed -i '' '/# Claude Code secure container alias/,/claude-secure.*--dangerously-skip-permissions'"'"'/d' "$SHELL_CONFIG" 2>/dev/null || true

# Add the yolo alias
cat >>"$SHELL_CONFIG" <<'EOF'

# Claude Code secure container alias
# 1Password SSH agent forwarding for commit signing (private keys stay on host)
alias yolo='docker run -it --rm \
  -e TERM=$TERM \
  -e COLORTERM=$COLORTERM \
  -e LANG=C.UTF-8 \
  -e LC_ALL=C.UTF-8 \
  -e GH_TOKEN=$(gh auth token) \
  -e SSH_AUTH_SOCK=/home/node/.1password/agent.sock \
  -e GIT_AUTHOR_NAME="$(git config user.name)" \
  -e GIT_AUTHOR_EMAIL="$(git config user.email)" \
  -e GIT_COMMITTER_NAME="$(git config user.name)" \
  -e GIT_COMMITTER_EMAIL="$(git config user.email)" \
  -e GIT_SIGNING_KEY="$(git config user.signingkey)" \
  -v $(pwd):/workspace \
  -v $HOME/.claude.json:/home/node/.claude.json \
  -v $HOME/.claude.local.json:/home/node/.claude.local.json \
  -v $HOME/.claude:/home/node/.claude \
  -v $HOME/.ssh:/home/node/.ssh:ro \
  -v $HOME/.1password/agent.sock:/home/node/.1password/agent.sock:ro \
  -w /workspace \
  claude-secure sh -c "git config --global user.name \"\$GIT_AUTHOR_NAME\" && git config --global user.email \"\$GIT_AUTHOR_EMAIL\" && git config --global user.signingkey \"\$GIT_SIGNING_KEY\" && git config --global gpg.format ssh && git config --global gpg.ssh.program \"op ssh sign\" && git config --global commit.gpgsign true && claude --dangerously-skip-permissions"'
EOF

echo "🎉 Done! Restart your terminal or run: source $SHELL_CONFIG"
echo ""
echo "Usage:"
echo "  cd your-project-folder"
echo "  yolo"
echo ""
echo "The container blocks all network access except npm, github, and anthropic servers."
