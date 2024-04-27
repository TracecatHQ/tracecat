import { redirect } from "next/navigation"
import { Clerk } from "@clerk/clerk-js"
import { auth } from "@clerk/nextjs/server"
import axios, { type InternalAxiosRequestConfig } from "axios"

import { authConfig } from "@/config/auth"
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
const __clerk = new Clerk(process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY!)

client.interceptors.request.use(async (config: InternalAxiosRequestConfig) => {
  const token = await getAuthToken()
  config.headers["Authorization"] = `Bearer ${token}`
  return config
})

export type Client = typeof client

/**
 * Gets the auth token, or redirects to the login page
 *
 * @returns The authentication token
 *
 */
export async function getAuthToken() {
  if (authConfig.disabled) {
    console.warn("Auth is disabled, using test token.")
    return "super-secret-token-32-characters-long"
  }
  let token: string | null | undefined
  if (isServer()) {
    token = await auth().getToken()
  } else {
    await __clerk.load()
    token = await __clerk.session?.getToken()
  }
  if (!token) {
    console.error("Failed to get authenticated client, redirecting to login")
    return redirect("/")
  }
  return token
}

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

export async function streamingResponse(endpoint: string, init?: RequestInit) {
  return fetch(`${process.env.NEXT_PUBLIC_API_URL}${endpoint}`, init)
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
