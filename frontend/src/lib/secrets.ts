import { z } from "zod"

import { CreateSecretParams, Secret, secretSchema } from "@/types/schemas"
import { client } from "@/lib/api"

export async function createSecret(secret: CreateSecretParams) {
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

export async function fetchAllSecrets(): Promise<Secret[]> {
  try {
    const response = await client.get<Secret[]>("/secrets")
    return z.array(secretSchema).parse(response.data)
  } catch (error) {
    console.error("Failed to add new credentials", error)
    throw error
  }
}

export async function deleteSecret(secretId: string): Promise<void> {
  try {
    await client.delete(`/secrets/${secretId}`)
  } catch (error) {
    console.error("Failed to delete secret", error)
    throw error
  }
}
