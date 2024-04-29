import axios, { type InternalAxiosRequestConfig } from "axios"

import { getAuthToken } from "@/lib/auth"
import { isServer } from "@/lib/utils"

// Determine the base URL based on the execution environment
let baseURL = process.env.NEXT_PUBLIC_API_URL

// Use different base url for server-side
if (process.env.NODE_ENV === "development" && isServer()) {
  baseURL = "http://host.docker.internal:8000"
}

export const client = axios.create({
  baseURL,
})

client.interceptors.request.use(async (config: InternalAxiosRequestConfig) => {
  const token = await getAuthToken()
  config.headers["Authorization"] = `Bearer ${token}`
  return config
})

export type Client = typeof client

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
