# Based on https://github.com/anthropics/claude-code/blob/main/.devcontainer/Dockerfile
FROM node:20

ARG TZ
ENV TZ="$TZ"

ARG CLAUDE_CODE_VERSION=latest
ARG GIT_DELTA_VERSION=0.18.2

# Install basic development tools and iptables/ipset
RUN apt-get update && apt-get install -y --no-install-recommends \
  less \
  git \
  procps \
  sudo \
  fzf \
  zsh \
  man-db \
  unzip \
  gnupg2 \
  gh \
  iptables \
  ipset \
  iproute2 \
  dnsutils \
  aggregate \
  jq \
  nano \
  vim \
  wget \
  curl \
  && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install fd-find (latest)
RUN ARCH=$(dpkg --print-architecture) && \
  FD_VERSION=$(curl -sL https://api.github.com/repos/sharkdp/fd/releases/latest | jq -r '.tag_name' | sed 's/^v//') && \
  wget "https://github.com/sharkdp/fd/releases/download/v${FD_VERSION}/fd_${FD_VERSION}_${ARCH}.deb" && \
  dpkg -i "fd_${FD_VERSION}_${ARCH}.deb" && \
  rm "fd_${FD_VERSION}_${ARCH}.deb"

# Install ast-grep (latest)
RUN ARCH=$(uname -m) && \
  case "$ARCH" in \
    x86_64) ARCH_NAME="x86_64-unknown-linux-gnu" ;; \
    aarch64) ARCH_NAME="aarch64-unknown-linux-gnu" ;; \
    *) echo "Unsupported architecture: $ARCH" && exit 1 ;; \
  esac && \
  AST_GREP_VERSION=$(curl -sL https://api.github.com/repos/ast-grep/ast-grep/releases/latest | jq -r '.tag_name') && \
  wget "https://github.com/ast-grep/ast-grep/releases/download/${AST_GREP_VERSION}/app-${ARCH_NAME}.zip" && \
  unzip "app-${ARCH_NAME}.zip" && \
  mv sg /usr/local/bin/ast-grep && \
  ln -s /usr/local/bin/ast-grep /usr/local/bin/sg && \
  rm "app-${ARCH_NAME}.zip"

# Install git-delta
RUN ARCH=$(dpkg --print-architecture) && \
  wget "https://github.com/dandavison/delta/releases/download/${GIT_DELTA_VERSION}/git-delta_${GIT_DELTA_VERSION}_${ARCH}.deb" && \
  dpkg -i "git-delta_${GIT_DELTA_VERSION}_${ARCH}.deb" && \
  rm "git-delta_${GIT_DELTA_VERSION}_${ARCH}.deb"

# Install 1Password CLI for SSH commit signing
RUN ARCH=$(dpkg --print-architecture) && \
  curl -sS https://downloads.1password.com/linux/keys/1password.asc | gpg --dearmor --output /usr/share/keyrings/1password-archive-keyring.gpg && \
  echo "deb [arch=${ARCH} signed-by=/usr/share/keyrings/1password-archive-keyring.gpg] https://downloads.1password.com/linux/debian/${ARCH} stable main" > /etc/apt/sources.list.d/1password.list && \
  mkdir -p /etc/debsig/policies/AC2D62742012EA22/ && \
  curl -sS https://downloads.1password.com/linux/debian/debsig/1password.pol > /etc/debsig/policies/AC2D62742012EA22/1password.pol && \
  mkdir -p /usr/share/debsig/keyrings/AC2D62742012EA22 && \
  curl -sS https://downloads.1password.com/linux/keys/1password.asc | gpg --dearmor --output /usr/share/debsig/keyrings/AC2D62742012EA22/debsig.gpg && \
  apt-get update && apt-get install -y 1password-cli && \
  apt-get clean && rm -rf /var/lib/apt/lists/*

# Install uv (Python package manager)
RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh

# Install pnpm
RUN corepack enable && corepack prepare pnpm@latest --activate

# Ensure default node user has access to /usr/local/share
RUN mkdir -p /usr/local/share/npm-global && \
  chown -R node:node /usr/local/share

ARG USERNAME=node

# Persist bash history
RUN SNIPPET="export PROMPT_COMMAND='history -a' && export HISTFILE=/commandhistory/.bash_history" \
  && mkdir /commandhistory \
  && touch /commandhistory/.bash_history \
  && chown -R $USERNAME /commandhistory

# Set `DEVCONTAINER` environment variable to help with orientation
ENV DEVCONTAINER=true

# Create workspace and config directories and set permissions
RUN mkdir -p /workspace /home/node/.claude && \
  chown -R node:node /workspace /home/node/.claude

WORKDIR /workspace

# Set up non-root user
USER node

# Install global packages
ENV NPM_CONFIG_PREFIX=/usr/local/share/npm-global
ENV PATH=$PATH:/usr/local/share/npm-global/bin

# Set the default shell to zsh rather than sh
ENV SHELL=/bin/zsh

# Set the default editor and visual
ENV EDITOR=nano
ENV VISUAL=nano

# Default powerline10k theme
ARG ZSH_IN_DOCKER_VERSION=1.2.0
RUN sh -c "$(wget -O- https://github.com/deluan/zsh-in-docker/releases/download/v${ZSH_IN_DOCKER_VERSION}/zsh-in-docker.sh)" -- \
  -p git \
  -p fzf \
  -a "source /usr/share/doc/fzf/examples/key-bindings.zsh" \
  -a "source /usr/share/doc/fzf/examples/completion.zsh" \
  -a "export PROMPT_COMMAND='history -a' && export HISTFILE=/commandhistory/.bash_history" \
  -a 'export PATH="$HOME/.local/bin:$PATH"' \
  -x

# Install Claude
RUN npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}

# Copy and set up firewall script
COPY init-firewall.sh /usr/local/bin/
USER root
RUN chmod +x /usr/local/bin/init-firewall.sh && \
  echo "node ALL=(root) NOPASSWD: /usr/local/bin/init-firewall.sh" > /etc/sudoers.d/node-firewall && \
  chmod 0440 /etc/sudoers.d/node-firewall
USER node
