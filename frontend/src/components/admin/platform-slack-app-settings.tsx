"use client"

import {
  CircleCheck,
  CircleDashed,
  Download,
  ExternalLink,
  Trash2,
  TriangleAlert,
} from "lucide-react"
import { type FormEvent, type ReactNode, useState } from "react"
import type { SlackPlatformAppRead } from "@/client"
import {
  CredentialField,
  StatusLabel,
} from "@/components/agents/external-channels/channel-bot-fields"
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
import { ProviderIcon } from "@/components/icons"
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
    markSlackAppSetupSteps,
    markSlackAppSetupStepsIsPending,
    removeSlackApp,
    removeSlackAppIsPending,
    removeSlackAppError,
  } = useAdminSlackApp()
  const [created, setCreated] = useState(false)
  const [editing, setEditing] = useState(false)

  if (slackAppIsLoading) {
    return <div className="text-muted-foreground">Loading...</div>
  }
  // A failed refetch keeps the loaded app, so typed credentials survive it.
  if (!slackApp) {
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
  // A removed app starts over at Create, not in a stale edit form.
  const removeAndReset = async () => {
    await removeSlackApp()
    setEditing(false)
    setCreated(false)
  }
  const removeAction = (
    <RemoveSlackApp
      onRemove={removeAndReset}
      removing={removeSlackAppIsPending}
    />
  )
  const removeError = getApiErrorDetail(removeSlackAppError)
  const removeErrorLine = removeError ? (
    <p className="text-sm text-destructive">{removeError}</p>
  ) : null
  const settingsFooter = (
    <div className="px-4 py-3">{manual("Slack settings for this app")}</div>
  )

  if (!registered) {
    return (
      <SlackAppCard
        status={<StatusLabel icon={CircleDashed}>Not registered</StatusLabel>}
      >
        <div className="space-y-4 p-4">
          <div className="space-y-1">
            <h4 className="text-sm font-medium">
              Create the Tracecat app in Slack
            </h4>
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
        </div>
        {created ? (
          <div className="space-y-4 p-4">
            <SetupDoneLine>Slack app ready</SetupDoneLine>
            <SlackAppCredentials
              slackApp={slackApp}
              onSave={saveSlackApp}
              saving={saveSlackAppIsPending}
            />
          </div>
        ) : null}
      </SlackAppCard>
    )
  }

  if (editing || slackApp.unreadable) {
    return (
      <SlackAppCard
        status={
          slackApp.unreadable ? (
            <StatusLabel icon={TriangleAlert} destructive>
              Needs credentials
            </StatusLabel>
          ) : (
            <StatusLabel icon={CircleCheck}>Registered</StatusLabel>
          )
        }
      >
        <div className="space-y-4 p-4">
          <SlackAppCredentials
            slackApp={slackApp}
            onSave={saveSlackApp}
            saving={saveSlackAppIsPending}
            onSaved={() => setEditing(false)}
            onCancel={slackApp.unreadable ? undefined : () => setEditing(false)}
            extraActions={removeAction}
          />
          {removeErrorLine}
        </div>
        {settingsFooter}
      </SlackAppCard>
    )
  }

  return (
    <SlackAppCard
      status={<StatusLabel icon={CircleCheck}>Registered</StatusLabel>}
    >
      <div className="space-y-2 p-4">
        <SetupDoneLine>App created in Slack</SetupDoneLine>
        <SetupDoneLine>Credentials saved</SetupDoneLine>
        <FinishInSlack
          slackApp={slackApp}
          onMark={markSlackAppSetupSteps}
          marking={markSlackAppSetupStepsIsPending}
        />
      </div>
      <SlackAppSummary
        slackApp={slackApp}
        onReplace={() => setEditing(true)}
        removeAction={removeAction}
      >
        {removeErrorLine}
      </SlackAppSummary>
      {settingsFooter}
    </SlackAppCard>
  )
}

/** The page's one surface: the app's identity and status, then its sections. */
function SlackAppCard({
  status,
  children,
}: {
  status: ReactNode
  children: ReactNode
}) {
  return (
    <section
      className="divide-y rounded-md border"
      aria-label="Tracecat for Slack"
    >
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 p-4">
        <ProviderIcon providerId="slack" className="size-10 p-2" />
        <div className="min-w-48 flex-1 space-y-0.5">
          <h3 className="text-sm font-semibold">Tracecat for Slack</h3>
          <p className="text-xs text-muted-foreground">
            Organizations with Agent Channels see Add to Slack on their
            agents&apos; Channels tab.
          </p>
        </div>
        {status}
      </div>
      {children}
    </section>
  )
}

/** The settings only Slack can change; ticks are saved with the app. */
function FinishInSlack({
  slackApp,
  onMark,
  marking,
}: {
  slackApp: SlackPlatformAppRead
  onMark: AdminSlackApp["markSlackAppSetupSteps"]
  marking: boolean
}) {
  const appId = slackApp.app_id
  const iconDone = slackApp.icon_uploaded
  const distributionDone = slackApp.distribution_enabled
  const mark = (step: Parameters<typeof onMark>[0]) =>
    void onMark(step).catch(() => undefined)

  if (iconDone && distributionDone) {
    return (
      <>
        <SetupDoneLine>Tracecat icon uploaded</SetupDoneLine>
        <SetupDoneLine>Public distribution on</SetupDoneLine>
      </>
    )
  }
  return (
    <div className="space-y-4 pt-4">
      <div className="space-y-1">
        <h4 className="text-sm font-medium">Finish in Slack</h4>
        <p className="text-xs text-muted-foreground">
          Tracecat can&apos;t check these two settings, so mark each one done
          once it&apos;s set.
        </p>
      </div>
      <TickOff
        title="Upload the Tracecat icon"
        done={iconDone}
        disabled={marking}
        onToggle={() => mark({ icon_uploaded: !iconDone })}
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
        disabled={marking}
        onToggle={() => mark({ distribution_enabled: !distributionDone })}
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
    </div>
  )
}

function TickOff({
  title,
  done,
  disabled,
  onToggle,
  actions,
  children,
}: {
  title: string
  done: boolean
  disabled: boolean
  onToggle: () => void
  actions?: ReactNode
  children: ReactNode
}) {
  const StateIcon = done ? CircleCheck : CircleDashed
  return (
    <div
      className="flex flex-wrap items-start gap-x-3 gap-y-2"
      role="group"
      aria-label={title}
    >
      <StateIcon
        className="mt-0.5 size-4 shrink-0 text-muted-foreground"
        aria-hidden
      />
      <div className="min-w-48 flex-1 space-y-0.5">
        <p className="text-sm">{title}</p>
        <p className="text-xs text-muted-foreground">{children}</p>
      </div>
      <div className="ml-7 flex flex-wrap items-center gap-2">
        {actions}
        <Button
          type="button"
          size="sm"
          variant={done ? "secondary" : "outline"}
          aria-pressed={done}
          disabled={disabled}
          onClick={onToggle}
        >
          {done ? "Done" : "Mark done"}
        </Button>
      </div>
    </div>
  )
}

/** The registered app at a glance, with its replace and remove actions. */
function SlackAppSummary({
  slackApp,
  onReplace,
  removeAction,
  children,
}: {
  slackApp: SlackPlatformAppRead
  onReplace: () => void
  removeAction: ReactNode
  /** The remove error, under the actions. */
  children: ReactNode
}) {
  const socket = slackApp.setup.transport === "socket"
  return (
    <div className="space-y-4 p-4">
      <dl className="grid grid-cols-[8rem_minmax(0,1fr)] gap-x-3 gap-y-2 text-xs">
        <dt className="text-muted-foreground">App ID</dt>
        <dd>
          <a
            href={slackAppSettingsUrl(slackApp.app_id, "general")}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 font-mono underline-offset-4 hover:underline"
          >
            {slackApp.app_id}
            <ExternalLink
              className="size-3 text-muted-foreground"
              aria-hidden
            />
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
      {children}
    </div>
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
          <Trash2 className="mr-2 size-4" />
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
    <form onSubmit={handleSubmit} className="space-y-4">
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-48 flex-1 space-y-1">
          <h4 className="text-sm font-medium">
            Paste the app&apos;s credentials
          </h4>
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
        <p className="flex items-start gap-2 text-sm text-destructive">
          <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
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
          appTokenPlaceholder={placeholder(
            slackApp.app_token_set,
            "xoxe.xapp-..."
          )}
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
