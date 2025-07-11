import axios from "axios"
import { env } from "next-runtime-env"
import { OpenAPI } from "@/client"

import { isServer } from "@/lib/utils"

/**
 *
 * @returns The base URL for the API based on the execution environment
 * Server side:
 * 1. NEXT_SERVER_API_URL (process.env)
 * 2. NEXT_PUBLIC_API_URL (process.env)
 * 3. http://api:8000
 *
 * Client side:
 * 1. NEXT_PUBLIC_API_URL (.env)
 * 2. http://localhost:8000
 */
export function getBaseUrl() {
  // Server side
  if (isServer()) {
    return (
      process.env.NEXT_SERVER_API_URL ??
      process.env.NEXT_PUBLIC_API_URL ??
      "http://api:8000"
    )
  }

  // Client side
  const baseUrl = env("NEXT_PUBLIC_API_URL")
  return baseUrl ?? "http://localhost:8000"
}

// Legacy axiosclient
export const client = axios.create({
  baseURL: getBaseUrl(),
  withCredentials: true,
})

export type Client = typeof client

OpenAPI.BASE = getBaseUrl()
OpenAPI.WITH_CREDENTIALS = true
