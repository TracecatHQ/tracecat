import { redirect } from "next/navigation"
import { type Session } from "@supabase/supabase-js"
import axios from "axios"

// Determine the base URL based on the execution environment
let baseURL = process.env.NEXT_PUBLIC_API_URL

// Use different base url for server-side
if (process.env.NODE_ENV === "development" && typeof window === "undefined") {
  baseURL = "http://host.docker.internal:8000"
}

const client = axios.create({
  baseURL,
})

export type Client = typeof client

export const getAuthenticatedClient = (session: Session | null) => {
  if (!session) {
    console.error("Failed to get authenticated client, redirecting to login")
    return redirect("/")
  }

  client.defaults.headers.common["Authorization"] =
    `Bearer ${session.access_token}`
  return client
}

export async function streamingResponse(endpoint: string, init?: RequestInit) {
  return fetch(`${process.env.NEXT_PUBLIC_API_URL}${endpoint}`, init)
}

/**
 * Wrapper around fetch to stream data from the server
 * We're using this over EventSource because we need authentication
 *
 * @param endpoint
 * @param session
 * @param init
 * @param delimiter
 * @returns
 */
export async function* streamGenerator(
  endpoint: string,
  session: Session | null,
  init?: RequestInit,
  delimiter: string = "\n"
) {
  if (!session) {
    console.error("Failed to get authenticated client, redirecting to login")
    return redirect("/")
  }
  const { headers, ...rest } = init ?? {}
  const response = await fetch(
    `${process.env.NEXT_PUBLIC_API_URL}${endpoint}`,
    {
      ...rest,
      headers: {
        ...headers, // Merge the headers from init object
        Authorization: `Bearer ${session?.access_token}`,
      },
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
