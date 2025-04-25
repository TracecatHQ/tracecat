import { UserRead, WorkspaceMembershipRead } from "@/client"
import { AxiosError } from "axios"

import { client } from "@/lib/api"

export const SYSTEM_USER: UserRead = {
  id: "system",
  email: "system@tracecat.com",
  role: "admin",
  first_name: "System",
  last_name: "",
  settings: {},
}

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

export function userIsPrivileged(
  user: UserRead | null,
  membership?: WorkspaceMembershipRead
): boolean {
  if (!user) {
    return false
  }
  return (
    user.is_superuser || user.role === "admin" || membership?.role === "admin"
  )
}

export function getDisplayName(user: UserRead) {
  if (!user.first_name) {
    return user.email.split("@")[0]
  } else if (user.last_name) {
    return `${user.first_name} ${user.last_name}`
  } else {
    return user.first_name
  }
}
