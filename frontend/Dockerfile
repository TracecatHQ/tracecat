# For development only

FROM node:21-slim AS base

EXPOSE 3000

ENV PNPM_HOME="/pnpm"
ENV PATH="$PNPM_HOME:$PATH"

RUN corepack enable
COPY . /app
WORKDIR /app

CMD [ "pnpm", "dev" ]
