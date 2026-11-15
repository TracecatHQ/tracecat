"use client"

import { ExternalLink } from "lucide-react"
import { type FormEvent, useEffect, useState } from "react"
import type { SlackPlatformAppRead } from "@/client"
import {
  CredentialField,
  EndpointField,
} from "@/components/agents/external-channels/channel-bot-fields"
import { buildSlackManifest } from "@/components/agents/external-channels/slack-manifest"
import { CopyField } from "@/components/copy-field"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import { useAdminSlackApp } from "@/hooks/use-admin"
import { getApiErrorDetail } from "@/lib/errors"
import { copyToClipboard } from "@/lib/utils"

const SLACK_APPS_URL = "https://api.slack.com/apps"

/** Register the one Slack app every organization installs with one click. */
export function PlatformSlackAppSettings() {
  const {
    slackApp,
    slackAppIsLoading,
    slackAppError,
    saveSlackApp,
    saveSlackAppIsPending,
    removeSlackApp,
    removeSlackAppIsPending,
  } = useAdminSlackApp()

  if (slackAppIsLoading) {
    return <div className="text-muted-foreground">Loading...</div>
  }
  if (slackAppError || !slackApp) {
    return (
      <p className="text-sm text-muted-foreground">
        {getApiErrorDetail(slackAppError) ?? "Failed to load the Slack app."}
      </p>
    )
  }
  return (
    <div className="space-y-10">
      <SlackAppSetup slackApp={slackApp} />
      <SlackAppCredentials
        slackApp={slackApp}
        onSave={saveSlackApp}
        saving={saveSlackAppIsPending}
        onRemove={removeSlackApp}
        removing={removeSlackAppIsPending}
      />
    </div>
  )
}

/** What the admin pastes into the Slack app's settings. */
function SlackAppSetup({ slackApp }: { slackApp: SlackPlatformAppRead }) {
  const { setup } = slackApp

  function handleCopyManifest(): void {
    const manifest = buildSlackManifest({
      endpointUrl: setup.request_url ?? null,
      oauthRedirectUrl: setup.oauth_redirect_url,
      appName: "Tracecat",
    })
    void copyToClipboard({
      value: JSON.stringify(manifest, null, 2),
      message: "Slack manifest copied",
    })
  }

  return (
    <section className="space-y-4">
      <h3 className="text-lg font-semibold tracking-tight">Slack setup</h3>
      <CopyField
        label="OAuth redirect URL"
        value={setup.oauth_redirect_url}
        help="Add under OAuth & Permissions in the Slack app."
      />
      {setup.request_url ? (
        <EndpointField
          label="Request URL"
          value={setup.request_url}
          help="Paste into both Event Subscriptions and Interactivity."
          provider="slack"
        />
      ) : (
        <p className="text-xs text-muted-foreground">
          This deployment receives Slack events over Socket Mode, so the app
          needs no request URL.
        </p>
      )}
      <p className="text-xs text-muted-foreground">
        Turn on public distribution under Manage Distribution so other Slack
        workspaces can install the app.
      </p>
      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={handleCopyManifest}
        >
          Copy app manifest
        </Button>
        <Button type="button" size="sm" variant="outline" asChild>
          <a href={SLACK_APPS_URL} target="_blank" rel="noreferrer">
            <ExternalLink className="mr-2 size-4" />
            Open Slack apps
          </a>
        </Button>
      </div>
    </section>
  )
}

/** The app's IDs and secrets; saved secrets are never shown, only kept. */
function SlackAppCredentials({
  slackApp,
  onSave,
  saving,
  onRemove,
  removing,
}: {
  slackApp: SlackPlatformAppRead
  onSave: ReturnType<typeof useAdminSlackApp>["saveSlackApp"]
  saving: boolean
  onRemove: ReturnType<typeof useAdminSlackApp>["removeSlackApp"]
  removing: boolean
}) {
  const [appId, setAppId] = useState(slackApp.app_id ?? "")
  const [clientId, setClientId] = useState(slackApp.client_id ?? "")
  const [clientSecret, setClientSecret] = useState("")
  const [signingSecret, setSigningSecret] = useState("")
  const [appToken, setAppToken] = useState("")
  const [appRefreshToken, setAppRefreshToken] = useState("")

  useEffect(() => {
    setAppId(slackApp.app_id ?? "")
    setClientId(slackApp.client_id ?? "")
  }, [slackApp.app_id, slackApp.client_id])

  const socket = slackApp.setup.transport === "socket"
  // Rotating app-level tokens come with a refresh token that Slack shows once.
  const rotatingAppToken = appToken.trim().startsWith("xoxe.")
  const registered = slackApp.app_id !== null || slackApp.unreadable
  // The API keeps a saved secret only while the app ID stays the same.
  const sameApp = slackApp.app_id !== null && appId.trim() === slackApp.app_id
  const has = (typed: string, saved: boolean) =>
    typed.trim() !== "" || (sameApp && saved)
  const canSave =
    appId.trim() !== "" &&
    clientId.trim() !== "" &&
    has(clientSecret, slackApp.client_secret_set) &&
    has(signingSecret, slackApp.signing_secret_set) &&
    (!socket || has(appToken, slackApp.app_token_set)) &&
    (!rotatingAppToken || appRefreshToken.trim() !== "")

  function placeholder(saved: boolean, empty: string): string {
    return sameApp && saved ? "Saved; leave blank to keep" : empty
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    try {
      await onSave({
        app_id: appId.trim(),
        client_id: clientId.trim(),
        client_secret: clientSecret.trim() || null,
        signing_secret: signingSecret.trim() || null,
        app_token: socket ? appToken.trim() || null : null,
        app_refresh_token:
          socket && rotatingAppToken ? appRefreshToken.trim() : null,
      })
    } catch {
      return
    }
    setClientSecret("")
    setSigningSecret("")
    setAppToken("")
    setAppRefreshToken("")
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <h3 className="text-lg font-semibold tracking-tight">Credentials</h3>
      {slackApp.unreadable ? (
        <p className="text-sm text-destructive">
          The saved Slack app no longer decrypts. Enter its credentials and save
          it again.
        </p>
      ) : null}
      <CredentialField
        id="slack-app-id"
        label="App ID"
        value={appId}
        onChange={setAppId}
        disabled={saving}
        placeholder="A0123456789"
      />
      <CredentialField
        id="slack-client-id"
        label="Client ID"
        value={clientId}
        onChange={setClientId}
        disabled={saving}
        placeholder="1234567890.1234567890"
      />
      <CredentialField
        id="slack-client-secret"
        label="Client secret"
        value={clientSecret}
        onChange={setClientSecret}
        disabled={saving}
        placeholder={placeholder(
          slackApp.client_secret_set,
          "From Basic Information"
        )}
        secret
      />
      <CredentialField
        id="slack-signing-secret"
        label="Signing secret"
        value={signingSecret}
        onChange={setSigningSecret}
        disabled={saving}
        placeholder={placeholder(
          slackApp.signing_secret_set,
          "From Basic Information"
        )}
        secret
      />
      {socket ? (
        <CredentialField
          id="slack-app-token"
          label="App-level token"
          value={appToken}
          onChange={(value) => {
            // A refresh token belongs to the token it was shown with.
            setAppToken(value)
            setAppRefreshToken("")
          }}
          disabled={saving}
          placeholder={placeholder(slackApp.app_token_set, "xapp-...")}
          help="An app-level token with connections:write, for Socket Mode."
          secret
        />
      ) : null}
      {socket && rotatingAppToken ? (
        <CredentialField
          id="slack-app-refresh-token"
          label="App-level refresh token"
          value={appRefreshToken}
          onChange={setAppRefreshToken}
          disabled={saving}
          placeholder="xoxe-..."
          help="Slack shows it once, beside the token."
          secret
        />
      ) : null}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" size="sm" disabled={!canSave || saving}>
          {saving ? "Saving..." : "Save"}
        </Button>
        {registered ? (
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={removing}
              >
                Remove
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Remove the Slack app</AlertDialogTitle>
                <AlertDialogDescription>
                  Every organization's install of this app stops answering in
                  Slack until the app is registered again.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Cancel</AlertDialogCancel>
                <AlertDialogAction onClick={() => void onRemove()}>
                  Remove
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        ) : null}
      </div>
    </form>
  )
}
