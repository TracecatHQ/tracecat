import { OpenAPI } from "@/client"
import axios, { type InternalAxiosRequestConfig } from "axios"

import { getAuthToken } from "@/lib/auth"
import { isServer } from "@/lib/utils"

/**
 *
 * @returns The base URL for the API based on the execution environment
 * Selection order:
 * 1. NEXT_SERVER_API_URL (process.env)
 * 2. NEXT_PUBLIC_API_URL (process.env)
 * 3. http://localhost:8000
 */
export function getBaseUrl() {
  // Server side
  if (isServer()) {
    return process.env.NEXT_SERVER_API_URL ?? process.env.NEXT_PUBLIC_API_URL
  }

  // Client side
  // @ts-expect-error Reason: Suppressing TypeScript error for the following code block
  if (global.API_URL === "__PLACEHOLDER_API_URL__") {
    return process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"
  } else {
    // @ts-expect-error Reason: Suppressing TypeScript error for the following code block
    return global.API_URL
  }
}

// Legacy axiosclient
export const client = axios.create({
  baseURL: getBaseUrl(),
})

client.interceptors.request.use(async (config: InternalAxiosRequestConfig) => {
  const token = await getAuthToken()
  config.headers["Authorization"] = `Bearer ${token}`
  return config
})

export type Client = typeof client

OpenAPI.BASE = getBaseUrl()

OpenAPI.interceptors.request.use(async (request) => {
  request.headers = {
    ...request.headers,
    Authorization: `Bearer ${await getAuthToken()}`,
  }
  return request
})

export async function authFetch(input: RequestInfo, init?: RequestInit) {
  const token = await getAuthToken()
  const { headers, ...rest } = init ?? {}
  const enhancedInit = {
    ...rest,
    headers: {
      ...headers,
      Authorization: `Bearer ${token}`,
    },
  }
  return await fetch(input, enhancedInit)
}

/**
 * Wrapper around fetch to stream data from the server
 * We're using this over EventSource because we need authentication
 *
 * @param endpoint
 * @param token
 * @param init
 * @param delimiter
 * @returns
 */
export async function* streamGenerator(
  endpoint: string,
  init?: RequestInit,
  delimiter: string = "\n"
) {
  const response = await authFetch(
    `${process.env.NEXT_PUBLIC_API_URL}${endpoint}`,
    {
      ...init,
      signal: new AbortController().signal,
    }
  )
  if (!response.body) {
    throw new Error("ReadableStream not supported")
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) {
        break
      }
      const chunk = decoder.decode(value, { stream: true })
      if (delimiter) {
        const lines = chunk.split(delimiter).filter((line) => line.length > 0)
        for (const line of lines) {
          yield line
        }
      } else {
        yield chunk
      }
    }
  } catch (error) {
    if ((error as Error).name === "AbortError") {
      console.log("Fetch aborted")
    } else {
      console.error("Error reading stream:", error)
    }
  } finally {
    reader.releaseLock()
  }
}
