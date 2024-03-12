import { redirect } from "next/navigation"
import { type Session } from "@supabase/supabase-js"
import axios from "axios"

const client = axios.create({
  baseURL: process.env.NEXT_PUBLIC_APP_URL,
})

export const getAuthenticatedClient = (session: Session | null) => {
  if (!session) {
    console.error("Failed to get authenticated client, redirecting to login")
    return redirect("/login")
  }

  client.defaults.headers.common["Authorization"] =
    `Bearer ${session.access_token}`
  return client
}

export type Client = typeof client
