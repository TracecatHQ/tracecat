import { UserRead, UserRole } from "@/client"
import { AxiosError } from "axios"

import { client } from "@/lib/api"

export async function getCurrentUser(): Promise<UserRead | null> {
  try {
    const response = await client.get("/users/me")
    return response.data as UserRead
  } catch (error) {
    if (error instanceof AxiosError) {
      // Backend throws 401 unauthorized if the user is not logged in
      console.log("User is not logged in")
      return null
    } else {
      console.error("Error fetching current user", error)
      throw error
    }
  }
}

export function userIsPrivileged(user: UserRead | null): boolean {
  if (!user) {
    return false
  }
  return user.is_superuser || user.role === "admin"
}
