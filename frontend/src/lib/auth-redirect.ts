import {
  normalizeMcpAuthReturnUrl,
  sanitizeReturnUrl,
} from "@/lib/auth-return-url"

type PostAuthRedirectPathParams = {
  isSuperuser: boolean
  eeMultiTenant: boolean
  returnUrl?: string | null
}

/**
 * Resolve the app route an authenticated user should land on after auth.
 */
export function getPostAuthRedirectPath({
  isSuperuser,
  eeMultiTenant,
  returnUrl,
}: PostAuthRedirectPathParams): string {
  const mcpAuthReturnUrl = normalizeMcpAuthReturnUrl(returnUrl)
  if (mcpAuthReturnUrl) {
    return mcpAuthReturnUrl
  }
  if (isSuperuser && eeMultiTenant) {
    return "/admin"
  }
  return returnUrl ?? "/workspaces"
}

/**
 * Resolve the client route that should decide the final post-auth destination.
 */
export function getPostAuthDecisionPath(returnUrl?: string | null): string {
  const sanitizedReturnUrl = sanitizeReturnUrl(returnUrl)
  if (!sanitizedReturnUrl) {
    return "/"
  }

  const params = new URLSearchParams()
  params.set("returnUrl", sanitizedReturnUrl)
  return `/sign-in?${params.toString()}`
}
