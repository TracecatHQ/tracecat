import { Session } from "@supabase/supabase-js"
import { z } from "zod"

import { Secret, secretSchema } from "@/types/schemas"
import { getAuthenticatedClient } from "@/lib/api"

export async function createSecret(
  maybeSession: Session | null,
  secret: Secret
) {
  try {
    const client = getAuthenticatedClient(maybeSession)
    await client.put("/secrets", JSON.stringify(secret), {
      headers: {
        "Content-Type": "application/json",
      },
    })
  } catch (error) {
    console.error("Failed to add new credentials", error)
  }
}

export async function fetchAllSecrets(maybeSession: Session | null) {
  try {
    const client = getAuthenticatedClient(maybeSession)
    const repsonse = await client.get<Secret[]>("/secrets")
    return z.array(secretSchema).parse(repsonse.data)
  } catch (error) {
    console.error("Failed to add new credentials", error)
    throw error
  }
}

export async function deleteSecret(
  maybeSession: Session | null,
  secretId: string
) {
  try {
    const client = getAuthenticatedClient(maybeSession)
    await client.delete(`/secrets/${secretId}`)
  } catch (error) {
    console.error("Failed to delete secret", error)
    throw error
  }
}
