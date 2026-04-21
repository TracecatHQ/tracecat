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
  if (isSuperuser && eeMultiTenant) {
    return "/admin"
  }
  return returnUrl ?? "/workspaces"
}
