"use client"

import { CopyField } from "@/components/copy-field"
import { useAppInfo } from "@/lib/hooks"
import { oauthRedirectUri } from "@/lib/integrations"

/**
 * Copyable Tracecat OAuth redirect URI, built from the public app URL.
 *
 * Use when no provider has been loaded yet, for example while creating a
 * custom provider or entering an MCP server's OAuth client.
 */
export function OAuthRedirectUriField({ help }: { help: string }) {
  const { appInfo } = useAppInfo()
  if (!appInfo?.public_app_url) {
    return null
  }
  return (
    <CopyField
      label="Redirect URI"
      value={oauthRedirectUri(appInfo.public_app_url)}
      help={help}
    />
  )
}
