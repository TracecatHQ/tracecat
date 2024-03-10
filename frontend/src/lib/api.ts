import { Session } from "@supabase/supabase-js"
import axios from "axios"

export const client = axios.create({
  baseURL: process.env.NEXT_PUBLIC_APP_URL,
})

export const getAuthenticatedClient = (session: Session) => {
  return axios.create({
    baseURL: process.env.NEXT_PUBLIC_APP_URL,
    headers: {
      Authorization: `Bearer ${session.access_token}`,
    },
  })
}
