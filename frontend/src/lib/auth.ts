import {
  ApiError,
  type UserRead,
  type UserRole,
  usersUsersCurrentUser,
  type WorkspaceMembershipRead,
} from "@/client"

/**
 * Minimal user info for display purposes (avatars, names).
 * Does not require platform role since it's not used for display.
 */
export interface UserDisplayInfo {
  id: string
  email: string
  first_name?: string | null
  last_name?: string | null
  settings?: Record<string, unknown>
}

export const SYSTEM_USER_READ: UserRead = {
  id: "system",
  email: "system@tracecat.com",
  role: "admin",
  first_name: "Tracecat",
  last_name: "",
  settings: {},
}

export async function getCurrentUser(): Promise<UserRead | null> {
  try {
    return await usersUsersCurrentUser()
  } catch (error) {
    if (error instanceof ApiError) {
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

export function getDisplayName(
  user: Pick<UserRead, "email" | "first_name" | "last_name">
) {
  if (!user.first_name) {
    return user.email.split("@")[0]
  } else if (user.last_name) {
    return `${user.first_name} ${user.last_name}`
  } else {
    return user.first_name
  }
}

/**
 * User class that wraps user data for display and authorization checks.
 * Accepts either full UserRead or minimal UserDisplayInfo for display-only contexts.
 */
export class User {
  constructor(private user: UserRead | UserDisplayInfo) {}

  get id(): string {
    return this.user.id
  }

  get email(): string {
    return this.user.email
  }

  get role(): UserRole | undefined {
    return "role" in this.user ? this.user.role : undefined
  }

  get firstName(): string | null | undefined {
    return this.user.first_name
  }

  get lastName(): string | null | undefined {
    return this.user.last_name
  }

  get settings(): Record<string, unknown> {
    return this.user.settings ?? {}
  }

  get isSuperuser(): boolean {
    return "is_superuser" in this.user
      ? (this.user.is_superuser ?? false)
      : false
  }

  get isActive(): boolean {
    return "is_active" in this.user ? (this.user.is_active ?? false) : false
  }

  get isVerified(): boolean {
    return "is_verified" in this.user ? (this.user.is_verified ?? false) : false
  }

  get unwrap(): UserRead | UserDisplayInfo {
    return this.user
  }

  /**
   * Returns true if the user is privileged in the context of this workspace.
   * Only works with full UserRead data.
   */
  isPrivileged(membership?: WorkspaceMembershipRead): boolean {
    if (!("role" in this.user)) return false
    return userIsPrivileged(this.user, membership)
  }

  /**
   * Returns true if the user is an organization admin.
   * Only works with full UserRead data.
   */
  isOrgAdmin(): boolean {
    if (!("role" in this.user)) return false
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

export const SYSTEM_USER = new User(SYSTEM_USER_READ)
