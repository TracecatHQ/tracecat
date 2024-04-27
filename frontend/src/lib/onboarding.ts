"use server"

import { auth, clerkClient } from "@clerk/nextjs/server"
import { AxiosError } from "axios"

import { client } from "@/lib/api"
import { createWorkflow } from "@/lib/flow"

/**
 * Initialize user settings in the database
 * @returns void
 */
export async function newUserFlow(): Promise<void> {
  console.log("Start new user flow")

  try {
    const response = await client.put("/users")
    if (response.status !== 201) {
      throw new Error("Unexpected response status")
    }
    console.log("New user created")
    await createWorkflow(
      "My first workflow",
      "Welcome to Tracecat. This is your first workflow!"
    )
    console.log("Created first workflow for new user")
  } catch (e) {
    if (e instanceof AxiosError) {
      if (e.response?.status !== 409) {
        throw new Error("Error creating new user")
      }
      console.log("User already exists")
    }
  }
}

export async function completeOnboarding(): Promise<{
  message?: any
  error?: string
}> {
  const { userId } = auth()

  if (!userId) {
    return { message: "No Logged In User" }
  }

  try {
    const res = await clerkClient.users.updateUser(userId, {
      publicMetadata: {
        onboardingComplete: true,
      },
    })
    return { message: res.publicMetadata }
  } catch (err) {
    return { error: "There was an error updating the user metadata." }
  }
}
