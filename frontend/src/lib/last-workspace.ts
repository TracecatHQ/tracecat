import Cookies from "js-cookie"

const LAST_WORKSPACE_COOKIE_PREFIX = "__tracecat:workspaces:last-viewed"
const LEGACY_LAST_WORKSPACE_COOKIE = LAST_WORKSPACE_COOKIE_PREFIX

function userLastWorkspaceCookieName(userId: string): string {
  return `${LAST_WORKSPACE_COOKIE_PREFIX}:${encodeURIComponent(userId)}`
}

/**
 * Reads the client-side last viewed workspace ID for a specific user.
 *
 * Anonymous callers use the legacy shared cookie, but authenticated users use a
 * user-scoped cookie so switching accounts in the same browser does not leak the
 * previously selected workspace across users.
 */
export function getLastWorkspaceIdForUser(userId?: string): string | undefined {
  if (!userId) {
    return Cookies.get(LEGACY_LAST_WORKSPACE_COOKIE)
  }
  return Cookies.get(userLastWorkspaceCookieName(userId))
}

/** Stores the client-side last viewed workspace ID for a specific user. */
export function setLastWorkspaceIdForUser(
  userId: string | undefined,
  workspaceId?: string
) {
  const cookieName = userId
    ? userLastWorkspaceCookieName(userId)
    : LEGACY_LAST_WORKSPACE_COOKIE

  if (!workspaceId) {
    Cookies.set(cookieName, "")
    return
  }
  Cookies.set(cookieName, workspaceId)
}

/** Clears the client-side last viewed workspace ID for a specific user. */
export function clearLastWorkspaceIdForUser(userId?: string) {
  if (!userId) {
    Cookies.remove(LEGACY_LAST_WORKSPACE_COOKIE)
    return
  }
  Cookies.remove(userLastWorkspaceCookieName(userId))
}
