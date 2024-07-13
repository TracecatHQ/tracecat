"use server"

import { auth, clerkClient } from "@clerk/nextjs/server"
import axios from "axios"

import { client } from "@/lib/api"

/**
 * Initialize user settings in the database
 * @returns void
 */

export async function newUserFlow(): Promise<void> {
  console.log("Start new user flow")

  try {
    const response = await client.post("/users")
    if (response.status !== 201) {
      throw new Error(`Unexpected response status: ${response.status}`)
    }
    console.log("New user created")
  } catch (e) {
    if (axios.isAxiosError(e) && e.response?.status == 409) {
      console.log("User already exists")
    } else {
      throw e
    }
  }
}


export async function completeOnboarding(): Promise<{
  message?: string | Record<string, unknown>
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
