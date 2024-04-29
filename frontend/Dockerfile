FROM node:21-slim AS base

ENV PNPM_HOME="/pnpm"
ENV PATH="$PNPM_HOME:$PATH"

RUN corepack enable
COPY . /app
WORKDIR /app


EXPOSE 3000
CMD [ "pnpm", "dev" ]
