"use client"

import { CheckIcon, Copy, ExternalLink } from "lucide-react"
import type { ReactNode } from "react"
import {
  CredentialField,
  EndpointField,
} from "@/components/agents/external-channels/channel-bot-fields"
import { slackCreateAppUrl } from "@/components/agents/external-channels/slack-manifest"
import { CopyField } from "@/components/copy-field"
import { Button } from "@/components/ui/button"
import { copyToClipboard } from "@/lib/utils"

/** Where each credential lives in the Slack app's Basic Information page. */
export const SLACK_CREDENTIAL_HINTS = {
  appId: "App Credentials → App ID",
  clientId: "App Credentials → Client ID",
  clientSecret: "App Credentials → Client Secret",
  signingSecret: "App Credentials → Signing Secret",
} as const

/** Opens Slack's create-app page in a new tab with the manifest filled in. */
export function CreateInSlackButton({
  manifest,
  disabled = false,
  onOpen,
}: {
  manifest: Record<string, unknown>
  disabled?: boolean
  onOpen?: () => void
}) {
  if (disabled) {
    return (
      <Button type="button" size="sm" disabled>
        Create in Slack
        <ExternalLink className="ml-2 size-4" />
      </Button>
    )
  }
  return (
    <Button type="button" size="sm" asChild>
      <a
        href={slackCreateAppUrl(manifest)}
        target="_blank"
        rel="noreferrer"
        onClick={onOpen}
      >
        Create in Slack
        <ExternalLink className="ml-2 size-4" />
      </a>
    </Button>
  )
}

/** A finished setup step, shown as a muted check line. */
export function SetupDoneLine({ children }: { children: ReactNode }) {
  return (
    <p className="flex items-center gap-2 text-xs text-muted-foreground">
      <CheckIcon className="size-4 shrink-0" aria-hidden />
      <span>{children}</span>
    </p>
  )
}

/** The URLs and manifest for setting up a Slack app without the prefilled link. */
export function SlackManualSetup({
  summary,
  requestUrl,
  oauthRedirectUrl,
  manifest,
}: {
  summary: string
  /** Null over Socket Mode, which needs no request URL. */
  requestUrl: string | null
  oauthRedirectUrl: string
  manifest: Record<string, unknown> | null
}) {
  function handleCopyManifest(): void {
    if (!manifest) {
      return
    }
    void copyToClipboard({
      value: JSON.stringify(manifest, null, 2),
      message: "Slack manifest copied",
    })
  }

  return (
    <details className="group text-xs">
      <summary className="cursor-pointer text-muted-foreground">
        {summary}
      </summary>
      <div className="mt-3 space-y-4">
        <p className="text-muted-foreground">
          Paste these into the matching Slack app settings.
        </p>
        <CopyField
          label="OAuth redirect URL"
          value={oauthRedirectUrl}
          help="OAuth & Permissions → Redirect URLs"
        />
        {requestUrl ? (
          <EndpointField
            label="Request URL"
            value={requestUrl}
            help="Event Subscriptions and Interactivity → Request URL"
            provider="slack"
          />
        ) : (
          <p className="text-muted-foreground">
            This deployment receives Slack events over Socket Mode. Turn on
            Settings → Socket Mode; no request URL is needed.
          </p>
        )}
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={handleCopyManifest}
          disabled={!manifest}
        >
          <Copy className="mr-2 size-4" />
          Copy app manifest
        </Button>
      </div>
    </details>
  )
}

/** The Socket Mode app-level token, and the refresh token a rotating one needs. */
export function SlackAppTokenFields({
  idPrefix,
  appToken,
  onAppTokenChange,
  refreshToken,
  onRefreshTokenChange,
  disabled,
  appTokenPlaceholder = "xoxe.xapp-...",
  refreshTokenHelp,
}: {
  idPrefix: string
  appToken: string
  onAppTokenChange: (value: string) => void
  refreshToken: string
  onRefreshTokenChange: (value: string) => void
  disabled: boolean
  appTokenPlaceholder?: string
  refreshTokenHelp?: string
}) {
  return (
    <div className="space-y-3">
      <div className="space-y-1">
        <p className="text-sm font-medium">App-level token for Socket Mode</p>
        <p className="text-xs text-muted-foreground">
          Basic Information → App-Level Tokens → Generate Token and Scopes. Add
          the <code>connections:write</code> scope, then copy both tokens Slack
          shows. Slack shows them once.
        </p>
      </div>
      <CredentialField
        id={`${idPrefix}-app-token`}
        label="Access token"
        secret
        value={appToken}
        onChange={onAppTokenChange}
        disabled={disabled}
        placeholder={appTokenPlaceholder}
      />
      <CredentialField
        id={`${idPrefix}-app-refresh-token`}
        label="Refresh token"
        secret
        value={refreshToken}
        onChange={onRefreshTokenChange}
        disabled={disabled}
        placeholder="xoxe-..."
        help={refreshTokenHelp}
      />
    </div>
  )
}
