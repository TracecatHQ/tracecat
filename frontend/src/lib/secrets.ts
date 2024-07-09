import { z } from "zod"

import { getSecretSchema, TCreateSecret, TGetSecret } from "@/types/schemas"
import { client } from "@/lib/api"

export async function createSecret(secret: TCreateSecret) {
  try {
    await client.post("/secrets", JSON.stringify(secret), {
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
    const response = await client.get<TGetSecret[]>("/secrets")
    return z.array(getSecretSchema).parse(response.data)
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
