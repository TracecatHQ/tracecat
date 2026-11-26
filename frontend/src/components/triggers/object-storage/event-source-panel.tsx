"use client"

import { AlertTriangleIcon } from "lucide-react"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { CopyButton } from "@/components/copy-button"
import { CenteredSpinner } from "@/components/loading/spinner"
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
 * An SNS event source: where to point the subscription, whether SNS has
 * confirmed it, and how to set it up safely.
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

  const topicArn = source.config.topic_arn
  const subscribeCommand = source.endpoint_url
    ? `aws sns subscribe --topic-arn ${shellQuote(topicArn)} --protocol https --notification-endpoint ${shellQuote(source.endpoint_url)}`
    : null

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
            {topicArn}
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
            see the URL SNS delivers to.
          </p>
        )}
      </div>

      <div className="space-y-2">
        <p className="text-xs font-medium">Set up the SNS topic</p>
        <ol className="list-decimal space-y-1.5 pl-4 text-xs text-muted-foreground">
          <li>
            Send the bucket's object created events to this topic (S3 event
            notifications).
          </li>
          <li>
            Subscribe the endpoint URL to the topic with the HTTPS protocol. The
            state above changes to confirmed once the subscription is confirmed.
          </li>
          <li>
            Leave raw message delivery off, so SNS sends its signed message
            envelope rather than the bare S3 event.
          </li>
          <li>
            Set a delivery retry policy that keeps retrying for 1 hour, so a
            short outage doesn't drop events.
          </li>
          <li>
            Add a dead-letter queue (SQS) to the subscription to keep the events
            SNS gives up on.
          </li>
        </ol>
        {subscribeCommand ? (
          <CopyableValue
            value={subscribeCommand}
            toastMessage="Command copied"
          />
        ) : null}
      </div>

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

/** Single-quote a value for a POSIX shell command line. */
export function shellQuote(value: string): string {
  return `'${value.replaceAll("'", `'"'"'`)}'`
}

function CopyableValue({
  value,
  toastMessage,
}: {
  value: string
  toastMessage: string
}) {
  return (
    <div className="flex min-w-0 max-w-full items-start gap-3 rounded-md bg-muted/60 px-3 py-2.5">
      <code className="min-w-0 flex-1 whitespace-pre-wrap break-all font-mono text-xs text-foreground/90 select-all">
        {value}
      </code>
      <CopyButton
        value={value}
        toastMessage={toastMessage}
        tooltipMessage="Copy"
        className="size-6 shrink-0"
        iconClassName="size-4 text-foreground/70"
      />
    </div>
  )
}
