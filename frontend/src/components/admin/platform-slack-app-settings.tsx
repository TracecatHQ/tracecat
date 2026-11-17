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
import type {
  SlackPlatformAppRead,
  SlackPlatformAppSetupStepsUpdate,
} from "@/client"
import {
  CredentialField,
  StatusLabel,
} from "@/components/agents/external-channels/channel-bot-fields"
import { PLATFORM_SLACK_TITLE } from "@/components/agents/external-channels/channel-bot-utils"
import {
  buildSlackPlatformManifest,
  slackAppSettingsUrl,
} from "@/components/agents/external-channels/slack-manifest"
import {
  CreateInSlackButton,
  SLACK_SAVED_SECRET_PLACEHOLDER,
  SlackAppTokenFields,
  SlackClientCredentialFields,
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

// Slack requires a square app icon between 512px and 2000px.
const TRACECAT_ICON_URL = "/tracecat-icon-1024.png"

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
              Slack opens with everything filled in. Choose a Slack workspace
              your team owns, then click Create.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            {/* Once opened, Save is the one primary action. */}
            <CreateInSlackButton
              manifest={manifest}
              variant={created ? "outline" : "default"}
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
              Can&apos;t read credentials
            </StatusLabel>
          ) : (
            <StatusLabel icon={CircleCheck}>Ready</StatusLabel>
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

  const mark = (step: SlackPlatformAppSetupStepsUpdate) =>
    void markSlackAppSetupSteps(step).catch(() => undefined)
  return (
    <SlackAppCard status={<StatusLabel icon={CircleCheck}>Ready</StatusLabel>}>
      <FinishInSlack
        slackApp={slackApp}
        onMark={mark}
        marking={markSlackAppSetupStepsIsPending}
      />
      <SlackAppSummary
        slackApp={slackApp}
        onMark={mark}
        marking={markSlackAppSetupStepsIsPending}
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
      aria-label={PLATFORM_SLACK_TITLE}
    >
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 p-4">
        <ProviderIcon providerId="slack" className="size-10 p-2" />
        <div className="min-w-48 flex-1 space-y-0.5">
          <h3 className="text-sm font-semibold">{PLATFORM_SLACK_TITLE}</h3>
          <p className="text-xs text-muted-foreground">
            Workspaces with Agent Channels see Add to Slack on their Channels
            page.
          </p>
        </div>
        {status}
      </div>
      {children}
    </section>
  )
}

type MarkSetupSteps = (step: SlackPlatformAppSetupStepsUpdate) => void

/** The settings only Slack can change, until each is marked done. */
function FinishInSlack({
  slackApp,
  onMark,
  marking,
}: {
  slackApp: SlackPlatformAppRead
  onMark: MarkSetupSteps
  marking: boolean
}) {
  if (slackApp.distribution_enabled && slackApp.icon_uploaded) {
    return null
  }
  return (
    <div className="space-y-4 p-4">
      {slackApp.distribution_enabled ? null : (
        <div
          className="flex flex-wrap items-start gap-x-3 gap-y-2"
          role="group"
          aria-label="Turn on public distribution"
        >
          <div className="min-w-48 flex-1 space-y-0.5">
            <h4 className="text-sm font-medium">
              Turn on public distribution (optional)
            </h4>
            <p className="text-xs text-muted-foreground">
              Until it&apos;s on, workspaces outside the one the app was created
              in can&apos;t add Tracecat. Slack doesn&apos;t report this, so
              mark it done once it&apos;s on.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button type="button" size="sm" variant="outline" asChild>
              <a
                href={slackAppSettingsUrl(slackApp.app_id, "distribute")}
                target="_blank"
                rel="noreferrer"
              >
                Open Manage Distribution
                <ExternalLink className="ml-2 size-4" />
              </a>
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={marking}
              onClick={() => onMark({ distribution_enabled: true })}
            >
              Mark done
            </Button>
          </div>
        </div>
      )}
      {slackApp.icon_uploaded ? null : (
        <div
          className="flex flex-wrap items-center gap-2"
          role="group"
          aria-label="Upload the Tracecat icon"
        >
          <p className="min-w-48 flex-1 text-xs text-muted-foreground">
            Optional: upload the Tracecat icon under Display Information.
          </p>
          <Button type="button" size="sm" variant="ghost" asChild>
            <a href={TRACECAT_ICON_URL} download="tracecat-icon.png">
              <Download className="mr-2 size-4" />
              Download logo
            </a>
          </Button>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={marking}
            onClick={() => onMark({ icon_uploaded: true })}
          >
            Mark done
          </Button>
        </div>
      )}
    </div>
  )
}

/** A setup step marked done, as a summary row with an undo for a wrong tick. */
function MarkedStep({
  label,
  children,
  undoLabel,
  disabled,
  onUndo,
}: {
  label: string
  children: ReactNode
  undoLabel: string
  disabled: boolean
  onUndo: () => void
}) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="flex items-center gap-1">
        <span>{children}</span>
        <span aria-hidden>·</span>
        <Button
          type="button"
          size="sm"
          variant="link"
          className="h-auto px-0 text-xs text-muted-foreground"
          aria-label={undoLabel}
          disabled={disabled}
          onClick={onUndo}
        >
          Undo
        </Button>
      </dd>
    </>
  )
}

/** The registered app at a glance, with its replace and remove actions. */
function SlackAppSummary({
  slackApp,
  onMark,
  marking,
  onReplace,
  removeAction,
  children,
}: {
  slackApp: SlackPlatformAppRead
  onMark: MarkSetupSteps
  marking: boolean
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
        {slackApp.distribution_enabled ? (
          <MarkedStep
            label="Distribution"
            undoLabel="Undo marking distribution on"
            disabled={marking}
            onUndo={() => onMark({ distribution_enabled: false })}
          >
            Marked on
          </MarkedStep>
        ) : null}
        {slackApp.icon_uploaded ? (
          <MarkedStep
            label="App icon"
            undoLabel="Undo marking the icon uploaded"
            disabled={marking}
            onUndo={() => onMark({ icon_uploaded: false })}
          >
            Marked uploaded
          </MarkedStep>
        ) : null}
      </dl>
      <div className="flex flex-wrap justify-between gap-2">
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
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="text-muted-foreground"
          disabled={removing}
        >
          <Trash2 className="mr-2 size-4" />
          Remove app
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
    return sameApp && saved ? SLACK_SAVED_SECRET_PLACEHOLDER : empty
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
            {slackApp.unreadable
              ? "Paste the app's credentials again"
              : "Paste the app's credentials"}
          </h4>
          <p className="text-xs text-muted-foreground">
            {slackApp.unreadable ? "The saved ones can't be read. " : null}
            All four are under Basic Information → App Credentials.
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
      <div className="grid gap-4 sm:grid-cols-2">
        <CredentialField
          id="slack-app-id"
          label="App ID"
          value={appId}
          onChange={setAppId}
          disabled={saving}
          placeholder="A0123456789"
        />
        <SlackClientCredentialFields
          idPrefix="slack-app"
          clientId={clientId}
          onClientIdChange={setClientId}
          clientSecret={clientSecret}
          onClientSecretChange={setClientSecret}
          signingSecret={signingSecret}
          onSigningSecretChange={setSigningSecret}
          clientSecretSaved={sameApp && slackApp.client_secret_set}
          signingSecretSaved={sameApp && slackApp.signing_secret_set}
          disabled={saving}
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
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" size="sm" disabled={!canSave || saving}>
            {saving ? "Saving..." : "Save"}
          </Button>
          {onCancel ? (
            <Button type="button" size="sm" variant="ghost" onClick={onCancel}>
              Keep current credentials
            </Button>
          ) : null}
          {canSave ? null : (
            <span className="text-xs text-muted-foreground">
              {socket
                ? "Fill in the credentials above to save."
                : "Fill in all four to save."}
            </span>
          )}
        </div>
        {extraActions}
      </div>
    </form>
  )
}
