# For development only

FROM node:22-slim AS base

EXPOSE 3000

ENV PNPM_HOME="/pnpm"
ENV PATH="$PNPM_HOME:$PATH"

RUN corepack enable
COPY . /app
WORKDIR /app

# Install dependencies inside the container to get correct platform-specific binaries (e.g., SWC for linux-arm64)
RUN pnpm install --frozen-lockfile

CMD [ "pnpm", "dev" ]
