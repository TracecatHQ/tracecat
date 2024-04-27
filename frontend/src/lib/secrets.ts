import { z } from "zod"

import { Secret, secretSchema } from "@/types/schemas"
import { client } from "@/lib/api"

export async function createSecret(secret: Secret) {
  try {
    await client.put("/secrets", JSON.stringify(secret), {
      headers: {
        "Content-Type": "application/json",
      },
    })
  } catch (error) {
    console.error("Failed to add new credentials", error)
  }
}

export async function fetchAllSecrets() {
  try {
    const repsonse = await client.get<Secret[]>("/secrets")
    return z.array(secretSchema).parse(repsonse.data)
  } catch (error) {
    console.error("Failed to add new credentials", error)
    throw error
  }
}

export async function deleteSecret(secretId: string) {
  try {
    await client.delete(`/secrets/${secretId}`)
  } catch (error) {
    console.error("Failed to delete secret", error)
    throw error
  }
}
