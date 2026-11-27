"use client"

import { AlertTriangleIcon, RefreshCwIcon } from "lucide-react"
import { useState } from "react"
import type { EventSourceRead } from "@/client"
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
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
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
  const { updateEventSource, rotateEventSourceEndpoint } =
    useTriggerMutations(workspaceId)
  const [confirmRotate, setConfirmRotate] = useState(false)

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

  async function handleRotate() {
    try {
      await rotateEventSourceEndpoint.mutateAsync(sourceId)
      setConfirmRotate(false)
      toast({
        title: "Endpoint URL rotated",
        description: "Subscribe the new URL, then delete the old subscription.",
      })
    } catch (err) {
      toast({
        title: "Couldn't rotate the endpoint URL",
        description: describeTriggerError(err),
        variant: "destructive",
      })
    }
  }

  const sender = provider?.sender ?? "the provider"

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
        <div className="flex items-center justify-between gap-2">
          <p className="text-xs font-medium">Endpoint URL</p>
          {canUpdate === true ? (
            <Button
              variant="outline"
              size="sm"
              className="h-7 text-xs"
              disabled={rotateEventSourceEndpoint.isPending}
              onClick={() => setConfirmRotate(true)}
            >
              <RefreshCwIcon className="mr-1.5 size-3" />
              Rotate URL
            </Button>
          ) : null}
        </div>
        <EndpointUrl
          source={source}
          canUpdate={canUpdate === true}
          sender={sender}
        />
        {source.previous_url_expires_at ? (
          <p className="text-xs text-muted-foreground">
            The old URL still accepts events until a subscription to this URL is
            confirmed, or until{" "}
            {new Date(source.previous_url_expires_at).toLocaleString()} at the
            latest. After that it refuses them for good.
          </p>
        ) : null}
      </div>

      {provider ? (
        <provider.Setup source={source} />
      ) : (
        <p className="text-xs text-muted-foreground">
          This version of Tracecat can't show setup steps for {source.kind}{" "}
          sources.
        </p>
      )}

      <AlertDialog open={confirmRotate} onOpenChange={setConfirmRotate}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Rotate the endpoint URL?</AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="space-y-3 text-sm text-muted-foreground">
                <p>
                  {/* The API hides a URL once a subscription to it was
                      confirmed; only those keep an overlap. */}
                  {source.endpoint_url === null
                    ? `The current URL keeps accepting events until a subscription to the new URL is confirmed, or for 24 hours at most. Then it refuses them for good: ${sender} sends them to the old subscription's dead-letter queue, or drops them if it has none.`
                    : "The current URL stops working now. No subscription to it was ever confirmed, so nothing is delivered to it."}
                </p>
                {provider ? (
                  <div className="space-y-1">
                    <p>Next, in {sender}:</p>
                    <ol className="list-decimal space-y-1 pl-4">
                      {provider.rotateSteps.map((step) => (
                        <li key={step}>{step}</li>
                      ))}
                    </ol>
                  </div>
                ) : null}
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={rotateEventSourceEndpoint.isPending}
              onClick={(event) => {
                // Stay open until the request settles, so a failure shows here.
                event.preventDefault()
                void handleRotate()
              }}
            >
              Rotate URL
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

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

/**
 * The URL the provider delivers to. The API returns it only to members who
 * can change triggers, and only until a subscription to it is confirmed.
 */
function EndpointUrl({
  source,
  canUpdate,
  sender,
}: {
  source: EventSourceRead
  canUpdate: boolean
  sender: string
}) {
  if (source.endpoint_url) {
    return (
      <>
        <CopyableValue
          value={source.endpoint_url}
          toastMessage="Endpoint URL copied"
        />
        <p className="text-xs text-muted-foreground">
          Treat it like a secret: the URL is how events reach this source. It is
          hidden once a subscription to it is confirmed.
        </p>
      </>
    )
  }
  if (canUpdate) {
    return (
      <p className="text-xs text-muted-foreground">
        Hidden: a subscription to this URL was confirmed. To point a new
        subscription here, rotate the URL.
      </p>
    )
  }
  return (
    <p className="text-xs text-muted-foreground">
      Hidden. Only members who can change triggers (trigger:update) can see the
      URL {sender} delivers to, until a subscription to it is confirmed.
    </p>
  )
}
