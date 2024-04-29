import { redirect } from "next/navigation"
import { Clerk } from "@clerk/clerk-js"
import {
  useClerk as __useClerk,
  useUser as __useClerkUser,
} from "@clerk/nextjs"
import {
  auth as __auth,
  currentUser as __currentUser,
} from "@clerk/nextjs/server"

import { authConfig } from "@/config/auth"
import { isServer } from "@/lib/utils"

export const auth = !authConfig.disabled
  ? __auth
  : () => {
      console.warn("Auth is disabled, using test token.")
      return {
        userId: null,
      } as ReturnType<typeof __auth>
    }

export const currentUser = !authConfig.disabled
  ? __currentUser
  : async () => {
      console.warn("Auth is disabled, using test user.")
      return null
    }
// Auth hooks
/**
 * If auth is enabled, returns the user object from Clerk.
 * If auth is disabled, returns a mock user object.
 * @returns useUser hook
 */
export function useUser() {
  if (authConfig.disabled) {
    return {
      user: null,
    } as ReturnType<typeof __useClerkUser>
  }
  return __useClerkUser()
}

export function useClerk() {
  if (authConfig.disabled) {
    return {
      signOut: () => {},
    } as ReturnType<typeof __useClerk>
  }
  return __useClerk()
}

const __clerk = authConfig.disabled
  ? undefined
  : new Clerk(process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY!)

/**
 * Gets the auth token, or redirects to the login page
 *
 * @returns The authentication token
 *
 */
export async function getAuthToken() {
  if (authConfig.disabled) {
    console.warn("Auth is disabled, using test token.")
    return "super-secret-token-32-characters-long"
  }
  let token: string | null | undefined
  if (isServer()) {
    token = await auth().getToken()
  } else {
    await __clerk?.load()
    token = await __clerk?.session?.getToken()
  }
  if (!token) {
    console.error("Failed to get authenticated client, redirecting to login")
    return redirect("/")
  }
  return token
}
