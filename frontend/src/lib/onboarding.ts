"use server"

import { auth, clerkClient } from "@clerk/nextjs/server"
import { AxiosError } from "axios"

import { client } from "@/lib/api"

/**
 * Initialize user settings in the database
 * @returns void
 */

export async function newUserFlow(): Promise<void> {
  console.log("Start new user flow")

  try {
    const response = await client.post("/users")
    console.log("New user created")
  } catch (e) {
    if (e instanceof AxiosError) {
      if (e.response?.status !== 409) {
        console.error(e.response?.data)
        throw new Error("Internal server error.")
      } else {
        console.log("User already exists")
      }
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
