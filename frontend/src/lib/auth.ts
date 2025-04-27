import { UserRead, UserRole, WorkspaceMembershipRead } from "@/client"
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
  return userIsOrgAdmin(user) || membership?.role === "admin"
}

export function userIsOrgAdmin(user?: UserRead | null): boolean {
  return user?.is_superuser || user?.role === "admin"
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

export class User {
  constructor(private user: UserRead) {}

  get id(): string {
    return this.user.id
  }

  get email(): string {
    return this.user.email
  }

  get role(): UserRole {
    return this.user.role
  }

  get firstName(): string | null | undefined {
    return this.user.first_name
  }

  get lastName(): string | null | undefined {
    return this.user.last_name
  }

  get settings(): Record<string, unknown> {
    return this.user.settings
  }

  get isSuperuser(): boolean {
    return this.user.is_superuser || false
  }

  get isActive(): boolean {
    return this.user.is_active || false
  }

  get isVerified(): boolean {
    return this.user.is_verified || false
  }

  get unwrap(): UserRead {
    return this.user
  }

  /**
   * Returns true if the user is privileged in the context of this workspace.
   */
  isPrivileged(membership?: WorkspaceMembershipRead): boolean {
    return userIsPrivileged(this.user, membership)
  }

  /**
   * Returns true if the user is an organization admin.
   */
  isOrgAdmin(): boolean {
    return userIsOrgAdmin(this.user)
  }

  /**
   * Returns true if the user is a workspace admin.
   */
  isWorkspaceAdmin(membership?: WorkspaceMembershipRead): boolean {
    return membership?.role === "admin"
  }

  /**
   * Returns the display name of the user.
   */
  getDisplayName(): string {
    return getDisplayName(this.user)
  }
}
