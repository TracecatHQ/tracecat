"use client"

import { CheckIcon, Copy, Download, ExternalLink } from "lucide-react"
import type { ReactNode } from "react"
import type { ChannelBotAppManifest } from "@/client"
import {
  CredentialField,
  EndpointField,
} from "@/components/agents/external-channels/channel-bot-fields"
import {
  copyOrDownloadAppManifest,
  isTextAppManifest,
} from "@/components/agents/external-channels/channel-bot-utils"
import { CopyField } from "@/components/copy-field"
import { Button } from "@/components/ui/button"

/** Opens the setup instructions for the custom Slack app flow on screen. */
export function CustomSlackAppDocsLink({
  legacy = false,
}: {
  legacy?: boolean
}) {
  const section = legacy ? "earlier-setup-flow" : "connect-a-slack-app"
  return (
    <a
      href={`https://docs.tracecat.com/agents/channels#${section}`}
      target="_blank"
      rel="noreferrer"
      className="underline underline-offset-2"
    >
      Custom Slack app setup guide
    </a>
  )
}

/** Where each credential lives in the Slack app's Basic Information page. */
export const SLACK_CREDENTIAL_HINTS = {
  appId: "App Credentials → App ID",
  clientId: "App Credentials → Client ID",
  clientSecret: "App Credentials → Client Secret",
  signingSecret: "App Credentials → Signing Secret",
} as const

/** Shown in a secret field whose saved value a blank entry keeps. */
export const SLACK_SAVED_SECRET_PLACEHOLDER = "Saved; leave blank to keep"

/** The Slack app's Client ID and its two secrets. */
export function SlackClientCredentialFields({
  idPrefix,
  clientId,
  onClientIdChange,
  clientSecret,
  onClientSecretChange,
  signingSecret,
  onSigningSecretChange,
  clientSecretSaved,
  signingSecretSaved,
  disabled,
}: {
  idPrefix: string
  clientId: string
  onClientIdChange: (value: string) => void
  clientSecret: string
  onClientSecretChange: (value: string) => void
  signingSecret: string
  onSigningSecretChange: (value: string) => void
  clientSecretSaved: boolean
  signingSecretSaved: boolean
  disabled: boolean
}) {
  const secretPlaceholder = (saved: boolean) =>
    saved ? SLACK_SAVED_SECRET_PLACEHOLDER : "Click Show, then copy"
  return (
    <>
      <CredentialField
        id={`${idPrefix}-client-id`}
        label="Client ID"
        value={clientId}
        onChange={onClientIdChange}
        disabled={disabled}
        placeholder="1234567890.1234567890"
        help={SLACK_CREDENTIAL_HINTS.clientId}
      />
      <CredentialField
        id={`${idPrefix}-client-secret`}
        label="Client secret"
        secret
        value={clientSecret}
        onChange={onClientSecretChange}
        disabled={disabled}
        placeholder={secretPlaceholder(clientSecretSaved)}
        help={SLACK_CREDENTIAL_HINTS.clientSecret}
      />
      <CredentialField
        id={`${idPrefix}-signing-secret`}
        label="Signing secret"
        secret
        value={signingSecret}
        onChange={onSigningSecretChange}
        disabled={disabled}
        placeholder={secretPlaceholder(signingSecretSaved)}
        help={SLACK_CREDENTIAL_HINTS.signingSecret}
      />
    </>
  )
}

/** Opens Slack's create-app page, which the API filled the manifest into, in a new tab. */
export function CreateInSlackButton({
  createUrl,
  disabled = false,
  onOpen,
}: {
  createUrl: string | null
  disabled?: boolean
  onOpen?: () => void
}) {
  if (disabled || !createUrl) {
    return (
      <Button type="button" size="sm" disabled>
        Create in Slack
        <ExternalLink className="ml-2 size-4" />
      </Button>
    )
  }
  return (
    <Button type="button" size="sm" asChild>
      <a href={createUrl} target="_blank" rel="noreferrer" onClick={onOpen}>
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
  manifest: ChannelBotAppManifest | null
}) {
  const text = manifest === null || isTextAppManifest(manifest)

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
          onClick={() => manifest && copyOrDownloadAppManifest(manifest)}
          disabled={!manifest}
        >
          {text ? (
            <Copy className="mr-2 size-4" />
          ) : (
            <Download className="mr-2 size-4" />
          )}
          {text ? "Copy app manifest" : "Download app manifest"}
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
