# https://github.com/vercel/next.js/blob/canary/examples/with-docker/Dockerfile

# Use the Node.js Alpine image as the base image for the deps stage
FROM node:22-alpine AS deps
WORKDIR /app

# Install necessary system libraries
RUN apk add --no-cache libc6-compat

# Copy only the necessary files to install dependencies
COPY package.json yarn.lock* package-lock.json* pnpm-lock.yaml* ./

# Install working version of corepack
RUN npm install -g corepack@0.31.0 && corepack --version

# Install dependencies with pnpm
RUN corepack enable pnpm && pnpm i --frozen-lockfile

# Use the Node.js Alpine image as the base image for the builder stage
FROM node:22-alpine AS builder
WORKDIR /app

# Define build arguments to pass environment variables
ARG NODE_ENV=production
ARG NEXT_PUBLIC_APP_ENV=production
ARG NEXT_PUBLIC_APP_URL=http://localhost:3000
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ARG NEXT_SERVER_API_URL=http://api:8000

# Set environment variables
ENV NODE_ENV=production
ENV NEXT_PUBLIC_APP_ENV=$NEXT_PUBLIC_APP_ENV
ENV NEXT_PUBLIC_APP_URL=$NEXT_PUBLIC_APP_URL
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
ENV NEXT_SERVER_API_URL=$NEXT_SERVER_API_URL

# Copy node_modules from the deps stage
COPY --from=deps /app/node_modules ./node_modules

# Install working version of corepack
RUN npm install -g corepack@0.31.0 && corepack --version

# Copy all source files and build the application
COPY . .
RUN corepack enable pnpm && pnpm build

# Use the Node.js Alpine image as the base image for the runner stage
FROM node:22-alpine AS runner
WORKDIR /app

# Set production environment variables
ENV NODE_ENV=production
ENV NEXT_PUBLIC_APP_ENV=production

# Create a non-root user to run the application
RUN addgroup --system --gid 1001 nodejs
RUN adduser --system --uid 1001 nextjs

# Copy public assets and built application from the builder stage
COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

# Switch to the next.js user
USER nextjs

# Expose the application port
EXPOSE 3000
ENV PORT=3000

# https://nextjs.org/docs/pages/api-reference/next-config-js/output#how-it-works
# NOTE: Fargate overrides HOSTNAME https://github.com/aws/containers-roadmap/issues/1325
CMD ["sh", "-c", "HOSTNAME=0.0.0.0 node server.js"]
