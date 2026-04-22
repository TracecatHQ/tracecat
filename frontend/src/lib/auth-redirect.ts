import { normalizeMcpAuthReturnUrl } from "@/lib/auth-return-url"

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
