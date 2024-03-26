import { redirect } from "next/navigation"
import { type Session } from "@supabase/supabase-js"
import axios from "axios"

const client = axios.create({
  baseURL: process.env.NEXT_PUBLIC_API_URL,
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

export async function* streamGenerator(
  endpoint: string,
  session: Session | null,
  init?: RequestInit
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
      yield decoder.decode(value, { stream: true })
    }
  } catch (error) {
    console.error("Error reading stream:", error)
  } finally {
    reader.releaseLock()
  }
}
