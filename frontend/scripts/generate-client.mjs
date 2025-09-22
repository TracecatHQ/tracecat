#!/usr/bin/env node
import { writeFileSync } from "node:fs"
import path from "node:path"
import { createClient } from "@hey-api/openapi-ts"

const DEFAULT_BASE_URL = "http://localhost:8080/api"
const FETCH_TIMEOUT_MS = Number(process.env.OPENAPI_FETCH_TIMEOUT_MS ?? 20000)
const FETCH_RETRIES = Number(process.env.OPENAPI_FETCH_RETRIES ?? 3)

function ensureTrailingSlash(value) {
  return value.endsWith("/") ? value : `${value}/`
}

function resolveSpecUrl() {
  const rootPath = process.env.TRACECAT__API_ROOT_PATH ?? "/api"
  const baseCandidates = [
    process.env.TRACECAT__PUBLIC_API_URL,
    process.env.NEXT_PUBLIC_API_URL &&
      `${process.env.NEXT_PUBLIC_API_URL}${rootPath}`,
    DEFAULT_BASE_URL,
  ].filter(Boolean)

  const base = ensureTrailingSlash(baseCandidates[0])
  return new URL("openapi.json", base).toString()
}

async function fetchWithRetry(url) {
  let attempt = 0
  let lastError
  while (attempt < FETCH_RETRIES) {
    attempt += 1
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS)
    try {
      const response = await fetch(url, { signal: controller.signal })
      if (!response.ok) {
        throw new Error(`Received ${response.status} ${response.statusText}`)
      }
      return await response.json()
    } catch (error) {
      lastError = error
      if (attempt < FETCH_RETRIES) {
        const backoff = 500 * attempt
        await new Promise((resolve) => setTimeout(resolve, backoff))
      }
    } finally {
      clearTimeout(timeout)
    }
  }
  throw lastError
}

function writeErrorLog(message) {
  const logPath = path.join(process.cwd(), `openapi-ts-error-${Date.now()}.log`)
  writeFileSync(logPath, message, { encoding: "utf8" })
  console.error(
    `\uD83D\uDD25 Unexpected error occurred. Log saved to ${logPath}`
  )
}

async function main() {
  const specUrl = resolveSpecUrl()
  console.log(`Fetching OpenAPI spec from ${specUrl}`)

  try {
    const spec = await fetchWithRetry(specUrl)
    await createClient({
      client: "axios",
      input: spec,
      output: {
        format: "prettier",
        lint: "eslint",
        path: "./src/client",
      },
    })
    console.log("✨ Done! Your client is located in: ./src/client")
  } catch (error) {
    const message =
      error instanceof Error ? (error.stack ?? error.message) : String(error)
    writeErrorLog(`Error downloading ${specUrl}\n${message}`)
    process.exitCode = 1
  }
}

main()
