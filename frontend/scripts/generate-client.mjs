#!/usr/bin/env node
"use strict";

import { spawn } from "node:child_process";
import { rm, writeFile, mkdtemp } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const DEFAULT_TIMEOUT_MS = 30_000;
const DEFAULT_RETRIES = 3;
const RETRY_DELAY_MS = 500;

const timeoutCandidates = [
  { key: "OPENAPI_GENERATE_TIMEOUT_MS", multiplier: 1 },
  { key: "OPENAPI_CLIENT_TIMEOUT_MS", multiplier: 1 },
  { key: "OPENAPI_GENERATE_TIMEOUT", multiplier: 1_000 },
  { key: "OPENAPI_CLIENT_TIMEOUT", multiplier: 1_000 },
];

const retryCandidates = [
  "OPENAPI_GENERATE_RETRIES",
  "OPENAPI_CLIENT_RETRIES",
];

function parseTimeout() {
  for (const candidate of timeoutCandidates) {
    const raw = process.env[candidate.key];
    if (!raw) {
      continue;
    }
    const parsed = Number(raw);
    if (!Number.isFinite(parsed) || parsed <= 0) {
      console.warn(
        `Ignoring invalid timeout value "${raw}" from ${candidate.key}; using fallback.`
      );
      continue;
    }
    return Math.trunc(parsed * candidate.multiplier);
  }
  return DEFAULT_TIMEOUT_MS;
}

function parseRetries() {
  for (const key of retryCandidates) {
    const raw = process.env[key];
    if (!raw) {
      continue;
    }
    const parsed = Number(raw);
    if (!Number.isFinite(parsed) || parsed < 1) {
      console.warn(
        `Ignoring invalid retry value "${raw}" from ${key}; using fallback.`
      );
      continue;
    }
    return Math.trunc(parsed);
  }
  return DEFAULT_RETRIES;
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function fetchWithRetry(url, { timeoutMs, retries }) {
  let lastError;
  for (let attempt = 1; attempt <= retries; attempt += 1) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);

    try {
      const response = await fetch(url, { signal: controller.signal });
      if (!response.ok) {
        throw new Error(`Received HTTP ${response.status}`);
      }
      return await response.text();
    } catch (error) {
      lastError = error;
      if (attempt < retries) {
        const backoff = RETRY_DELAY_MS * attempt;
        console.warn(
          `Failed to download OpenAPI spec (${error.message}); retry ${attempt} of ${retries} in ${backoff}ms`
        );
        await delay(backoff);
      }
    } finally {
      clearTimeout(timer);
    }
  }
  throw lastError;
}

async function run() {
  const baseUrl = process.env.TRACECAT__PUBLIC_API_URL ?? "http://localhost/api";
  const specUrl = new URL("/openapi.json", baseUrl).toString();
  const timeoutMs = parseTimeout();
  const retries = parseRetries();

  const tempDir = await mkdtemp(path.join(os.tmpdir(), "tracecat-openapi-"));
  const specPath = path.join(tempDir, "openapi.json");

  try {
    console.log(
      `Downloading OpenAPI spec from ${specUrl} (timeout=${timeoutMs}ms, retries=${retries})`
    );
    const specContents = await fetchWithRetry(specUrl, {
      timeoutMs,
      retries,
    });
    await writeFile(specPath, specContents, "utf8");

    const scriptDir = path.dirname(fileURLToPath(import.meta.url));
    const cliRelative = path.resolve(
      scriptDir,
      "../node_modules/.bin/openapi-ts"
    );
    const cliExecutable =
      process.platform === "win32" ? `${cliRelative}.cmd` : cliRelative;

    const args = [
      "--input",
      specPath,
      "--output",
      "./src/client",
      "--client",
      "axios",
    ];

    await new Promise((resolve, reject) => {
      const child = spawn(cliExecutable, args, {
        stdio: "inherit",
        shell: false,
      });

      child.on("error", reject);
      child.on("close", (code) => {
        if (code === 0) {
          resolve();
        } else {
          reject(new Error(`openapi-ts exited with code ${code}`));
        }
      });
    });
  } finally {
    await rm(tempDir, { recursive: true, force: true });
  }
}

run().catch((error) => {
  console.error("Failed to generate client:", error);
  process.exitCode = 1;
});
