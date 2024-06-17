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
    const response = await client.put("/users")
    if (response.status !== 201) {
      throw new Error("Unexpected response status")
    }
    console.log("New user created")
  } catch (e) {
    if (e instanceof AxiosError) {
      if (e.response?.status !== 409) {
        if (process.env.TRACECAT__APP_ENV !== "production") {
          throw new Error(
            "Internal Server Error. Please check API service logs to debug."
          )
        } else {
          throw new Error(
            "Unexpected error creating new user in production. Try running in development mode for more detailed error logs."
          )
        }
      }
      console.log("User already exists")
    } else {
      throw e // Re-throw non-Axios errors
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
