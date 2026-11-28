"use client"

import { AlertTriangleIcon } from "lucide-react"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { CenteredSpinner } from "@/components/loading/spinner"
import { CopyableValue } from "@/components/triggers/object-storage/copyable-value"
import {
  describeEventSource,
  getEventSourceProvider,
} from "@/components/triggers/object-storage/event-source-providers"
import { describeTriggerError } from "@/components/triggers/trigger-errors"
import {
  SubscriptionStatusLabel,
  TriggerStatusLabel,
} from "@/components/triggers/trigger-status"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Switch } from "@/components/ui/switch"
import { toast } from "@/components/ui/use-toast"
import { useEventSource, useTriggerMutations } from "@/hooks/use-triggers"

/**
 * An event source: where its provider delivers, whether the feed is
 * confirmed, and the provider's setup steps (`EVENT_SOURCE_PROVIDERS`).
 */
export function EventSourcePanel({
  workspaceId,
  sourceId,
  keyPrefix,
}: {
  workspaceId: string
  sourceId: string
  /** The reading trigger's prefix, for the loop warning. */
  keyPrefix: string
}) {
  const canUpdate = useScopeCheck("trigger:update")
  const {
    data: source,
    isLoading,
    error,
  } = useEventSource(workspaceId, sourceId)
  const { updateEventSource } = useTriggerMutations(workspaceId)

  if (isLoading) {
    return <CenteredSpinner />
  }
  if (error || !source) {
    return (
      <p className="text-xs text-destructive">
        Couldn't load the event source.{" "}
        {error ? describeTriggerError(error) : ""}
      </p>
    )
  }

  const provider = getEventSourceProvider(source.kind)

  async function setOnline(online: boolean) {
    try {
      await updateEventSource.mutateAsync({
        sourceId,
        requestBody: { status: online ? "online" : "offline" },
      })
    } catch (err) {
      toast({
        title: online
          ? "Couldn't put the source online"
          : "Couldn't take the source offline",
        description: describeTriggerError(err),
        variant: "destructive",
      })
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 space-y-1">
          <p className="text-sm font-medium">{source.name}</p>
          <p className="truncate font-mono text-xs text-muted-foreground">
            {describeEventSource(source)}
          </p>
          <div className="flex items-center gap-4 pt-1">
            <SubscriptionStatusLabel status={source.subscription_status} />
            <TriggerStatusLabel status={source.status} />
          </div>
        </div>
        <Switch
          aria-label={`${source.name} online`}
          checked={source.status === "online"}
          disabled={canUpdate !== true || updateEventSource.isPending}
          onCheckedChange={setOnline}
        />
      </div>

      <div className="space-y-2">
        <p className="text-xs font-medium">Endpoint URL</p>
        {source.endpoint_url ? (
          <>
            <CopyableValue
              value={source.endpoint_url}
              toastMessage="Endpoint URL copied"
            />
            <p className="text-xs text-muted-foreground">
              Treat it like a secret: the URL is how events reach this source,
              and it doesn't change.
            </p>
          </>
        ) : (
          <p className="text-xs text-muted-foreground">
            Hidden. Only members who can change triggers (trigger:update) can
            see the URL {provider?.sender ?? "the provider"} delivers to.
          </p>
        )}
      </div>

      {provider ? (
        <provider.Setup workspaceId={workspaceId} source={source} />
      ) : (
        <p className="text-xs text-muted-foreground">
          This version of Tracecat can't show setup steps for {source.kind}{" "}
          sources.
        </p>
      )}

      <Alert>
        <AlertTriangleIcon className="size-4" />
        <AlertTitle className="text-xs">Avoid loops</AlertTitle>
        <AlertDescription className="text-xs text-muted-foreground">
          A workflow or agent that writes to this bucket under{" "}
          {keyPrefix ? (
            <code className="font-mono">{keyPrefix}</code>
          ) : (
            "any key"
          )}{" "}
          starts itself again for every object it writes. Write results under
          another prefix or to another bucket.
        </AlertDescription>
      </Alert>
    </div>
  )
}
