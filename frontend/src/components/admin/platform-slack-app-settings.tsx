"use client"

import { Download, ExternalLink } from "lucide-react"
import { type FormEvent, type ReactNode, useState } from "react"
import type { SlackPlatformAppRead } from "@/client"
import { CredentialField } from "@/components/agents/external-channels/channel-bot-fields"
import {
  buildSlackPlatformManifest,
  slackAppSettingsUrl,
} from "@/components/agents/external-channels/slack-manifest"
import {
  CreateInSlackButton,
  SetupDoneLine,
  SLACK_CREDENTIAL_HINTS,
  SlackAppTokenFields,
  SlackManualSetup,
} from "@/components/agents/external-channels/slack-setup-fields"
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

const TRACECAT_ICON_URL = "/icon.png"

type AdminSlackApp = ReturnType<typeof useAdminSlackApp>

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
    removeSlackAppError,
  } = useAdminSlackApp()
  const [created, setCreated] = useState(false)
  const [editing, setEditing] = useState(false)

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

  const registered = slackApp.app_id !== null || slackApp.unreadable
  const manifest = buildSlackPlatformManifest({
    endpointUrl: slackApp.setup.request_url ?? null,
    oauthRedirectUrl: slackApp.setup.oauth_redirect_url,
  })
  const manual = (summary: string) => (
    <SlackManualSetup
      summary={summary}
      requestUrl={slackApp.setup.request_url ?? null}
      oauthRedirectUrl={slackApp.setup.oauth_redirect_url}
      manifest={manifest}
    />
  )
  const removeAction = (
    <RemoveSlackApp
      onRemove={removeSlackApp}
      removing={removeSlackAppIsPending}
    />
  )
  const removeError = getApiErrorDetail(removeSlackAppError)

  if (!registered) {
    return (
      <div className="space-y-6">
        <section className="space-y-4 rounded-md border p-4">
          <div className="space-y-1">
            <h3 className="text-sm font-medium">
              Create the Tracecat app in Slack
            </h3>
            <p className="text-xs text-muted-foreground">
              Slack opens with the name, permissions,{" "}
              {slackApp.setup.transport === "socket"
                ? "Socket Mode"
                : "event URLs"}{" "}
              and token rotation already filled in. Pick a workspace your team
              owns and click Create.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <CreateInSlackButton
              manifest={manifest}
              onOpen={() => setCreated(true)}
            />
            {created ? null : (
              <Button
                type="button"
                size="sm"
                variant="link"
                className="h-auto px-0 text-xs text-muted-foreground"
                onClick={() => setCreated(true)}
              >
                I already have the app
              </Button>
            )}
          </div>
          {manual("Create the app by hand instead")}
        </section>
        {created ? (
          <>
            <SetupDoneLine>App created in Slack</SetupDoneLine>
            <SlackAppCredentials
              slackApp={slackApp}
              onSave={saveSlackApp}
              saving={saveSlackAppIsPending}
            />
          </>
        ) : null}
      </div>
    )
  }

  if (editing || slackApp.unreadable) {
    return (
      <div className="space-y-6">
        <SlackAppCredentials
          slackApp={slackApp}
          onSave={saveSlackApp}
          saving={saveSlackAppIsPending}
          onSaved={() => setEditing(false)}
          onCancel={slackApp.unreadable ? undefined : () => setEditing(false)}
          extraActions={removeAction}
        />
        {removeError ? (
          <p className="text-sm text-destructive">{removeError}</p>
        ) : null}
        {manual("Slack settings for this app")}
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <SetupDoneLine>App created in Slack</SetupDoneLine>
        <SetupDoneLine>Credentials saved</SetupDoneLine>
      </div>
      <FinishInSlack appId={slackApp.app_id} />
      <SlackAppSummary
        slackApp={slackApp}
        onReplace={() => setEditing(true)}
        removeAction={removeAction}
        removeError={removeError}
      >
        {manual("Slack settings for this app")}
      </SlackAppSummary>
    </div>
  )
}

/** The settings only Slack can change; ticks live in this page only. */
function FinishInSlack({ appId }: { appId: string | null }) {
  const [iconDone, setIconDone] = useState(false)
  const [distributionDone, setDistributionDone] = useState(false)

  if (iconDone && distributionDone) {
    return (
      <div className="space-y-2">
        <SetupDoneLine>Tracecat icon uploaded</SetupDoneLine>
        <SetupDoneLine>Public distribution on</SetupDoneLine>
      </div>
    )
  }
  return (
    <section className="space-y-3 rounded-md border p-4">
      <div className="space-y-1">
        <h3 className="text-sm font-medium">
          Two things only Slack&apos;s settings can do
        </h3>
        <p className="text-xs text-muted-foreground">
          Tracecat can&apos;t check these, so tick each one when it&apos;s done.
        </p>
      </div>
      <TickOff
        title="Upload the Tracecat icon"
        done={iconDone}
        onToggle={() => setIconDone(!iconDone)}
        actions={
          <>
            <Button type="button" size="sm" variant="outline" asChild>
              <a href={TRACECAT_ICON_URL} download="tracecat-icon.png">
                <Download className="mr-2 size-4" />
                Download logo
              </a>
            </Button>
            <Button type="button" size="sm" variant="outline" asChild>
              <a
                href={slackAppSettingsUrl(appId, "general")}
                target="_blank"
                rel="noreferrer"
              >
                Open Basic Information
                <ExternalLink className="ml-2 size-4" />
              </a>
            </Button>
          </>
        }
      >
        Basic Information → Display Information → App icon.
      </TickOff>
      <TickOff
        title="Turn on public distribution"
        done={distributionDone}
        onToggle={() => setDistributionDone(!distributionDone)}
        actions={
          <Button type="button" size="sm" variant="outline" asChild>
            <a
              href={slackAppSettingsUrl(appId, "distribute")}
              target="_blank"
              rel="noreferrer"
            >
              Open Manage Distribution
              <ExternalLink className="ml-2 size-4" />
            </a>
          </Button>
        }
      >
        Manage Distribution → Activate Public Distribution. Without it, only
        your own Slack workspace can install the app.
      </TickOff>
    </section>
  )
}

function TickOff({
  title,
  done,
  onToggle,
  actions,
  children,
}: {
  title: string
  done: boolean
  onToggle: () => void
  actions?: ReactNode
  children: ReactNode
}) {
  return (
    <div
      className="space-y-2 rounded-md border px-3 py-2"
      role="group"
      aria-label={title}
    >
      <div className="flex flex-wrap items-center gap-2">
        <p className="min-w-40 flex-1 text-xs font-medium">{title}</p>
        {actions}
        <Button
          type="button"
          size="sm"
          variant={done ? "secondary" : "outline"}
          aria-pressed={done}
          onClick={onToggle}
        >
          {done ? "Done" : "Mark done"}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">{children}</p>
    </div>
  )
}

/** The registered app at a glance, with its replace and remove actions. */
function SlackAppSummary({
  slackApp,
  onReplace,
  removeAction,
  removeError,
  children,
}: {
  slackApp: SlackPlatformAppRead
  onReplace: () => void
  removeAction: ReactNode
  removeError: string | null
  children: ReactNode
}) {
  const socket = slackApp.setup.transport === "socket"
  return (
    <section
      className="space-y-4 rounded-md border p-4"
      aria-label="Tracecat for Slack"
    >
      <div className="space-y-1">
        <h3 className="text-sm font-medium">Tracecat for Slack</h3>
        <p className="text-xs text-muted-foreground">
          Organizations with Agent Channels see Add to Slack on their
          agents&apos; Channels tab.
        </p>
      </div>
      <dl className="grid grid-cols-[8rem_minmax(0,1fr)] gap-x-3 gap-y-2 text-xs">
        <dt className="text-muted-foreground">App ID</dt>
        <dd>
          <a
            href={slackAppSettingsUrl(slackApp.app_id, "general")}
            target="_blank"
            rel="noreferrer"
            className="font-mono underline-offset-4 hover:underline"
          >
            {slackApp.app_id}
          </a>
        </dd>
        <dt className="text-muted-foreground">Slack events</dt>
        <dd>{socket ? "Socket Mode" : "HTTP"}</dd>
        <dt className="text-muted-foreground">Token rotation</dt>
        <dd>On</dd>
      </dl>
      <div className="flex flex-wrap gap-2">
        <Button type="button" size="sm" variant="outline" onClick={onReplace}>
          Replace credentials
        </Button>
        {removeAction}
      </div>
      {removeError ? (
        <p className="text-sm text-destructive">{removeError}</p>
      ) : null}
      {children}
    </section>
  )
}

function RemoveSlackApp({
  onRemove,
  removing,
}: {
  onRemove: AdminSlackApp["removeSlackApp"]
  removing: boolean
}) {
  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <Button type="button" size="sm" variant="outline" disabled={removing}>
          Remove
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Remove the Slack app</AlertDialogTitle>
          <AlertDialogDescription>
            Removal is refused while any organization still has the app
            installed.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            onClick={() => void onRemove().catch(() => undefined)}
          >
            Remove
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

/** The app's IDs and secrets; saved secrets are never shown, only kept. */
function SlackAppCredentials({
  slackApp,
  onSave,
  saving,
  onSaved,
  onCancel,
  extraActions,
}: {
  slackApp: SlackPlatformAppRead
  onSave: AdminSlackApp["saveSlackApp"]
  saving: boolean
  onSaved?: () => void
  onCancel?: () => void
  extraActions?: ReactNode
}) {
  const [appId, setAppId] = useState(slackApp.app_id ?? "")
  const [clientId, setClientId] = useState(slackApp.client_id ?? "")
  const [clientSecret, setClientSecret] = useState("")
  const [signingSecret, setSigningSecret] = useState("")
  const [appToken, setAppToken] = useState("")
  const [appRefreshToken, setAppRefreshToken] = useState("")

  const socket = slackApp.setup.transport === "socket"
  const rotatingAppToken = appToken.trim().startsWith("xoxe.")
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
    onSaved?.()
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4 rounded-md border p-4">
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-48 flex-1 space-y-1">
          <h3 className="text-sm font-medium">
            Paste the app&apos;s credentials
          </h3>
          <p className="text-xs text-muted-foreground">
            In the Slack app, open Basic Information. Everything below is on
            that page.
          </p>
        </div>
        <Button type="button" size="sm" variant="outline" asChild>
          <a
            href={slackAppSettingsUrl(appId, "general")}
            target="_blank"
            rel="noreferrer"
          >
            Open Basic Information
            <ExternalLink className="ml-2 size-4" />
          </a>
        </Button>
      </div>
      {slackApp.unreadable ? (
        <p className="text-sm text-destructive">
          The saved Slack app no longer decrypts. Enter its credentials and save
          it again.
        </p>
      ) : null}
      <div className="grid gap-4 sm:grid-cols-2">
        <CredentialField
          id="slack-app-id"
          label="App ID"
          value={appId}
          onChange={setAppId}
          disabled={saving}
          placeholder="A0123456789"
          help={SLACK_CREDENTIAL_HINTS.appId}
        />
        <CredentialField
          id="slack-client-id"
          label="Client ID"
          value={clientId}
          onChange={setClientId}
          disabled={saving}
          placeholder="1234567890.1234567890"
          help={SLACK_CREDENTIAL_HINTS.clientId}
        />
        <CredentialField
          id="slack-client-secret"
          label="Client secret"
          value={clientSecret}
          onChange={setClientSecret}
          disabled={saving}
          placeholder={placeholder(
            slackApp.client_secret_set,
            "Click Show, then copy"
          )}
          help={SLACK_CREDENTIAL_HINTS.clientSecret}
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
            "Click Show, then copy"
          )}
          help={SLACK_CREDENTIAL_HINTS.signingSecret}
          secret
        />
      </div>
      {socket ? (
        <SlackAppTokenFields
          idPrefix="slack"
          appToken={appToken}
          onAppTokenChange={setAppToken}
          refreshToken={appRefreshToken}
          onRefreshTokenChange={setAppRefreshToken}
          disabled={saving}
          appTokenPlaceholder={placeholder(slackApp.app_token_set, "xapp-...")}
        />
      ) : null}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" size="sm" disabled={!canSave || saving}>
          {saving ? "Saving..." : "Save"}
        </Button>
        {onCancel ? (
          <Button type="button" size="sm" variant="ghost" onClick={onCancel}>
            Keep current credentials
          </Button>
        ) : null}
        {extraActions}
      </div>
    </form>
  )
}
