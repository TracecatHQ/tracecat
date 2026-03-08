"use client"

import type { DialogProps } from "@radix-ui/react-dialog"
import { AlertTriangle, CheckCircle2, ExternalLink } from "lucide-react"
import { useEffect, useState } from "react"
import type {
  AwsCredentialSyncConfigRead,
  AwsCredentialSyncConfigUpdate,
  CredentialSyncResult,
} from "@/client"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Separator } from "@/components/ui/separator"
import { useWorkspaceDetails } from "@/hooks/use-workspace"
import { useAwsCredentialSync } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

interface AwsCredentialSyncDialogProps extends DialogProps {
  onOpenChange: (open: boolean) => void
}

interface AwsCredentialSyncFormState {
  region: string
  secretPrefix: string
  accessKeyId: string
  secretAccessKey: string
  sessionToken: string
}

const EMPTY_FORM_STATE: AwsCredentialSyncFormState = {
  region: "",
  secretPrefix: "",
  accessKeyId: "",
  secretAccessKey: "",
  sessionToken: "",
}

function getInitialFormState(
  config: AwsCredentialSyncConfigRead | undefined
): AwsCredentialSyncFormState {
  return {
    region: config?.region ?? "",
    secretPrefix: config?.secret_prefix ?? "",
    accessKeyId: "",
    secretAccessKey: "",
    sessionToken: "",
  }
}

function formatResultSummary(result: CredentialSyncResult): string {
  const parts = [
    `${result.processed ?? 0} processed`,
    `${result.created ?? 0} created`,
    `${result.updated ?? 0} updated`,
  ]
  if ((result.failed ?? 0) > 0) {
    parts.push(`${result.failed} failed`)
  }
  return parts.join(" • ")
}

function maskHint(isConfigured: boolean | undefined, label: string): string {
  return isConfigured
    ? `${label} is already stored. Leave blank to keep the current value.`
    : `${label} is required before you can run a sync.`
}

export function AwsCredentialSyncDialog({
  open,
  onOpenChange,
}: AwsCredentialSyncDialogProps) {
  const workspaceId = useWorkspaceId()
  const { workspace } = useWorkspaceDetails()
  const {
    awsCredentialSyncConfig,
    awsCredentialSyncConfigIsLoading,
    refetchAwsCredentialSyncConfig,
    updateAwsCredentialSyncConfig,
    isUpdatingAwsCredentialSyncConfig,
    pushAwsCredentialSync,
    isPushingAwsCredentialSync,
    pullAwsCredentialSync,
    isPullingAwsCredentialSync,
  } = useAwsCredentialSync(workspaceId, { configEnabled: open })
  const [formState, setFormState] =
    useState<AwsCredentialSyncFormState>(EMPTY_FORM_STATE)
  const [hasInitializedForm, setHasInitializedForm] = useState(false)
  const [inlineError, setInlineError] = useState<string | null>(null)
  const [lastResult, setLastResult] = useState<CredentialSyncResult | null>(
    null
  )

  useEffect(() => {
    if (!open) {
      setHasInitializedForm(false)
      setFormState(EMPTY_FORM_STATE)
      setInlineError(null)
      setLastResult(null)
      return
    }
    if (!hasInitializedForm && !awsCredentialSyncConfigIsLoading) {
      setFormState(getInitialFormState(awsCredentialSyncConfig))
      setHasInitializedForm(true)
    }
  }, [
    open,
    hasInitializedForm,
    awsCredentialSyncConfig,
    awsCredentialSyncConfigIsLoading,
  ])

  const isBusy =
    isUpdatingAwsCredentialSyncConfig ||
    isPushingAwsCredentialSync ||
    isPullingAwsCredentialSync
  const isSyncConfigured = awsCredentialSyncConfig?.is_configured === true

  function updateField<K extends keyof AwsCredentialSyncFormState>(
    key: K,
    value: AwsCredentialSyncFormState[K]
  ) {
    setFormState((current) => ({
      ...current,
      [key]: value,
    }))
  }

  async function handleSaveSettings() {
    const region = formState.region.trim()
    const secretPrefix = formState.secretPrefix.trim()
    const accessKeyId = formState.accessKeyId.trim()
    const secretAccessKey = formState.secretAccessKey.trim()
    const sessionToken = formState.sessionToken.trim()

    if (!region) {
      setInlineError("AWS region is required.")
      return
    }
    if (!secretPrefix) {
      setInlineError("Secret prefix is required.")
      return
    }
    if (!accessKeyId && awsCredentialSyncConfig?.has_access_key_id !== true) {
      setInlineError("AWS access key ID is required.")
      return
    }
    if (
      !secretAccessKey &&
      awsCredentialSyncConfig?.has_secret_access_key !== true
    ) {
      setInlineError("AWS secret access key is required.")
      return
    }

    const payload: AwsCredentialSyncConfigUpdate = {
      region,
      secret_prefix: secretPrefix,
    }
    if (accessKeyId) {
      payload.access_key_id = accessKeyId
    }
    if (secretAccessKey) {
      payload.secret_access_key = secretAccessKey
    }
    if (sessionToken) {
      payload.session_token = sessionToken
    }

    setInlineError(null)
    await updateAwsCredentialSyncConfig(payload)
    await refetchAwsCredentialSyncConfig()
    setFormState((current) => ({
      ...current,
      accessKeyId: "",
      secretAccessKey: "",
      sessionToken: "",
    }))
  }

  async function handlePushAll() {
    setInlineError(null)
    const result = await pushAwsCredentialSync()
    setLastResult(result)
  }

  async function handlePullAll() {
    setInlineError(null)
    const result = await pullAwsCredentialSync()
    setLastResult(result)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>AWS Secrets Manager sync</DialogTitle>
          <DialogDescription>
            These settings are organization-wide. Push and pull only apply to
            the current workspace.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-6">
          {awsCredentialSyncConfig?.is_corrupted ? (
            <Alert variant="warning">
              <AlertTriangle className="size-4" />
              <AlertTitle>Stored settings are unreadable</AlertTitle>
              <AlertDescription>
                Save the AWS settings again to overwrite the corrupted
                organization configuration before running a sync.
              </AlertDescription>
            </Alert>
          ) : null}

          {inlineError ? (
            <Alert variant="destructive">
              <AlertTriangle className="size-4" />
              <AlertTitle>Unable to save settings</AlertTitle>
              <AlertDescription>{inlineError}</AlertDescription>
            </Alert>
          ) : null}

          <section className="space-y-3">
            <div className="space-y-1">
              <h3 className="text-sm font-medium">Current workspace</h3>
              <p className="text-sm text-muted-foreground">
                Push and pull will sync credentials for{" "}
                <span className="font-medium text-foreground">
                  {workspace?.name ?? workspaceId}
                </span>{" "}
                only.
              </p>
            </div>
            <div className="rounded-md border px-4 py-3 text-sm">
              <div className="font-medium">
                Organization-wide AWS configuration
              </div>
              <p className="mt-1 text-muted-foreground">
                Future secret sync providers can reuse this same on-demand flow.
                For now this dialog only configures AWS Secrets Manager.
              </p>
            </div>
          </section>

          <Separator />

          <section className="space-y-4">
            <div className="space-y-1">
              <h3 className="text-sm font-medium">AWS settings</h3>
              <p className="text-sm text-muted-foreground">
                Save this once per organization, then run push or pull on
                demand.
              </p>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="aws-sync-region">Region</Label>
                <Input
                  id="aws-sync-region"
                  value={formState.region}
                  onChange={(event) =>
                    updateField("region", event.target.value)
                  }
                  placeholder="us-east-1"
                  disabled={awsCredentialSyncConfigIsLoading || isBusy}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="aws-sync-prefix">Secret prefix</Label>
                <Input
                  id="aws-sync-prefix"
                  value={formState.secretPrefix}
                  onChange={(event) =>
                    updateField("secretPrefix", event.target.value)
                  }
                  placeholder="tracecat/credentials"
                  disabled={awsCredentialSyncConfigIsLoading || isBusy}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="aws-sync-access-key-id">Access key ID</Label>
                <Input
                  id="aws-sync-access-key-id"
                  value={formState.accessKeyId}
                  onChange={(event) =>
                    updateField("accessKeyId", event.target.value)
                  }
                  placeholder={
                    awsCredentialSyncConfig?.has_access_key_id
                      ? "Stored in organization settings"
                      : "AKIA..."
                  }
                  disabled={awsCredentialSyncConfigIsLoading || isBusy}
                />
                <p className="text-xs text-muted-foreground">
                  {maskHint(
                    awsCredentialSyncConfig?.has_access_key_id,
                    "Access key ID"
                  )}
                </p>
              </div>
              <div className="space-y-2">
                <Label htmlFor="aws-sync-secret-access-key">
                  Secret access key
                </Label>
                <Input
                  id="aws-sync-secret-access-key"
                  type="password"
                  value={formState.secretAccessKey}
                  onChange={(event) =>
                    updateField("secretAccessKey", event.target.value)
                  }
                  placeholder={
                    awsCredentialSyncConfig?.has_secret_access_key
                      ? "Stored in organization settings"
                      : "Required"
                  }
                  disabled={awsCredentialSyncConfigIsLoading || isBusy}
                />
                <p className="text-xs text-muted-foreground">
                  {maskHint(
                    awsCredentialSyncConfig?.has_secret_access_key,
                    "Secret access key"
                  )}
                </p>
              </div>
            </div>

            <div className="space-y-2">
              <Label htmlFor="aws-sync-session-token">
                Session token{" "}
                <span className="text-muted-foreground">(optional)</span>
              </Label>
              <Input
                id="aws-sync-session-token"
                type="password"
                value={formState.sessionToken}
                onChange={(event) =>
                  updateField("sessionToken", event.target.value)
                }
                placeholder={
                  awsCredentialSyncConfig?.has_session_token
                    ? "Stored in organization settings"
                    : "Only needed for temporary credentials"
                }
                disabled={awsCredentialSyncConfigIsLoading || isBusy}
              />
              <p className="text-xs text-muted-foreground">
                {maskHint(
                  awsCredentialSyncConfig?.has_session_token,
                  "Session token"
                )}
              </p>
            </div>

            <div className="flex justify-end">
              <Button
                onClick={handleSaveSettings}
                disabled={awsCredentialSyncConfigIsLoading || isBusy}
              >
                Save settings
              </Button>
            </div>
          </section>

          <Separator />

          <section className="space-y-4">
            <div className="space-y-1">
              <h3 className="text-sm font-medium">Run sync</h3>
              <p className="text-sm text-muted-foreground">
                This is an on-demand batch sync. It does not schedule background
                jobs and it never deletes missing local credentials.
              </p>
            </div>

            {!isSyncConfigured ? (
              <Alert>
                <AlertTriangle className="size-4" />
                <AlertTitle>Save valid AWS settings first</AlertTitle>
                <AlertDescription>
                  Push and pull are disabled until the organization AWS sync
                  configuration is complete.
                </AlertDescription>
              </Alert>
            ) : null}

            <div className="flex flex-col gap-2 sm:flex-row sm:justify-end">
              <Button
                type="button"
                variant="outline"
                onClick={handlePushAll}
                disabled={!isSyncConfigured || isBusy}
              >
                Push all to AWS
              </Button>
              <Button
                type="button"
                variant="outline"
                onClick={handlePullAll}
                disabled={!isSyncConfigured || isBusy}
              >
                Pull all from AWS
              </Button>
            </div>
          </section>

          {lastResult ? (
            <>
              <Separator />
              <section className="space-y-3">
                <Alert
                  variant={(lastResult.failed ?? 0) > 0 ? "warning" : "default"}
                >
                  {(lastResult.failed ?? 0) > 0 ? (
                    <AlertTriangle className="size-4" />
                  ) : (
                    <CheckCircle2 className="size-4" />
                  )}
                  <AlertTitle>
                    Last {lastResult.operation ?? "sync"} result
                  </AlertTitle>
                  <AlertDescription>
                    {formatResultSummary(lastResult)}
                  </AlertDescription>
                </Alert>

                {(lastResult.errors?.length ?? 0) > 0 ? (
                  <div className="space-y-2 rounded-md border px-4 py-3 text-sm">
                    <div className="font-medium">Failures</div>
                    <div className="space-y-2">
                      {lastResult.errors?.map((error, index) => (
                        <div
                          key={`${error.remote_name ?? error.secret_name}-${index}`}
                          className="rounded border px-3 py-2"
                        >
                          <div className="font-medium">
                            {error.secret_name}
                            {error.environment ? ` (${error.environment})` : ""}
                          </div>
                          <p className="mt-1 text-muted-foreground">
                            {error.message}
                          </p>
                          {error.remote_name ? (
                            <p className="mt-1 flex items-center gap-1 text-xs text-muted-foreground">
                              <ExternalLink className="size-3" />
                              {error.remote_name}
                            </p>
                          ) : null}
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}
              </section>
            </>
          ) : null}
        </div>
      </DialogContent>
    </Dialog>
  )
}
