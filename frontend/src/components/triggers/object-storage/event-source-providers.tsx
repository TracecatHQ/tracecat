"use client"

import { type ComponentType, useState } from "react"
import type {
  EventSourceCreate,
  EventSourceKind,
  EventSourceRead,
} from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { CopyableValue } from "@/components/triggers/object-storage/copyable-value"
import { describeTriggerError } from "@/components/triggers/trigger-errors"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { toast } from "@/components/ui/use-toast"
import { useTriggerMutations } from "@/hooks/use-triggers"

/** The settings of a new event source, a union over source kinds. */
export type EventSourceConfig = EventSourceCreate["config"]

/** The provider-specific fields of a new source, as typed so far. */
export type ProviderDraft = Record<string, string>

/** Props the object storage form passes to a provider's new-source fields. */
export interface ProviderFieldsProps {
  draft: ProviderDraft
  onDraftChange: (draft: ProviderDraft) => void
  disabled: boolean
}

/** Props the event source panel passes to a provider's setup steps. */
export interface ProviderSetupProps {
  workspaceId: string
  source: EventSourceRead
}

/**
 * Everything the object storage UI needs to know about one storage provider:
 * how its events reach Tracecat (the event source kind), the fields to create
 * a source, how to describe a source, and how to set the feed up. The trigger
 * form, the event source panel and the lists read only this.
 */
export interface EventSourceProviderDefinition {
  kind: EventSourceKind
  /** Shown in the provider picker, for example "Amazon S3 (via SNS)". */
  label: string
  /** The fields' starting values. */
  emptyDraft: ProviderDraft
  Fields: ComponentType<ProviderFieldsProps>
  /** The API config for a draft, or an error message to show. */
  buildConfig: (draft: ProviderDraft) => EventSourceConfig | string
  /** One line naming where a source's events come from. */
  describeSource: (source: EventSourceRead) => string
  /** Who calls the endpoint URL, for "the URL ... delivers to". */
  sender: string
  Setup: ComponentType<ProviderSetupProps>
}

/** Single-quote a value for a POSIX shell command line. */
export function shellQuote(value: string): string {
  return `'${value.replaceAll("'", `'"'"'`)}'`
}

function SnsFields({ draft, onDraftChange, disabled }: ProviderFieldsProps) {
  return (
    <>
      <Input
        aria-label="SNS topic ARN"
        className="font-mono text-xs"
        placeholder="arn:aws:sns:us-east-1:123456789012:bucket-events"
        value={draft.topicArn ?? ""}
        required
        disabled={disabled}
        onChange={(event) =>
          onDraftChange({ ...draft, topicArn: event.target.value })
        }
      />
      <p className="text-xs text-muted-foreground">
        The standard SNS topic your bucket publishes object events to. FIFO
        topics can't deliver to HTTPS endpoints.
      </p>
      <Input
        aria-label="S3 bucket"
        className="font-mono text-xs"
        placeholder="Bucket name or ARN (optional)"
        value={draft.bucket ?? ""}
        disabled={disabled}
        onChange={(event) =>
          onDraftChange({ ...draft, bucket: event.target.value })
        }
      />
      <p className="text-xs text-muted-foreground">
        Accept events for this bucket only. Leave empty to accept every bucket
        that publishes to the topic. Keep the topic policy's bucket and account
        conditions too: the pin reads the bucket named in each message.
      </p>
    </>
  )
}

/** The source's bucket pin: shown to everyone, changed with trigger:update. */
function SnsBucketPin({ workspaceId, source }: ProviderSetupProps) {
  const canUpdate = useScopeCheck("trigger:update")
  const { updateEventSource } = useTriggerMutations(workspaceId)
  const saved = source.config.bucket ?? ""
  const [bucket, setBucket] = useState(saved)
  const changed = bucket.trim() !== saved

  async function save(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const pin = bucket.trim()
    try {
      const updated = await updateEventSource.mutateAsync({
        sourceId: source.id,
        requestBody: {
          config: {
            kind: "aws_sns",
            topic_arn: source.config.topic_arn,
            bucket: pin || null,
          },
        },
      })
      // The API saves an ARN as the bucket name.
      setBucket(updated.config.bucket ?? "")
    } catch (err) {
      toast({
        title: pin ? "Couldn't pin the bucket" : "Couldn't clear the bucket",
        description: describeTriggerError(err),
        variant: "destructive",
      })
    }
  }

  return (
    <form className="space-y-2" onSubmit={save}>
      <p className="text-xs font-medium">Bucket</p>
      {canUpdate === true ? (
        <div className="flex gap-2">
          <Input
            aria-label="Pinned S3 bucket"
            className="font-mono text-xs"
            placeholder="Any bucket (name or ARN to pin one)"
            value={bucket}
            disabled={updateEventSource.isPending}
            onChange={(event) => setBucket(event.target.value)}
          />
          <Button
            type="submit"
            size="sm"
            variant="outline"
            disabled={!changed || updateEventSource.isPending}
          >
            {bucket.trim() || !saved ? "Save" : "Clear"}
          </Button>
        </div>
      ) : (
        <p className="font-mono text-xs text-muted-foreground">
          {saved || "Any bucket"}
        </p>
      )}
      <p className="text-xs text-muted-foreground">
        {saved
          ? `Events for any bucket other than ${saved} are refused.`
          : "Every bucket that publishes to the topic is accepted."}
      </p>
      {saved ? (
        <p className="text-xs text-muted-foreground">
          The pin reads the bucket named in each message. Keep the topic
          policy's bucket and account conditions too.
        </p>
      ) : null}
    </form>
  )
}

function SnsSetup({ workspaceId, source }: ProviderSetupProps) {
  const subscribeCommand = source.endpoint_url
    ? `aws sns subscribe --topic-arn ${shellQuote(source.config.topic_arn)} --protocol https --notification-endpoint ${shellQuote(source.endpoint_url)}`
    : null
  return (
    <div className="space-y-6">
      <SnsBucketPin
        // Remount on another source or a new saved value, so the field
        // starts from what is saved.
        key={`${source.id}:${source.config.bucket ?? ""}`}
        workspaceId={workspaceId}
        source={source}
      />
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
    </div>
  )
}

/**
 * Every storage provider the object storage trigger can read, by event source
 * kind. Adding a provider is one entry here plus its fields and setup steps.
 */
export const EVENT_SOURCE_PROVIDERS: Record<
  EventSourceKind,
  EventSourceProviderDefinition
> = {
  aws_sns: {
    kind: "aws_sns",
    label: "Amazon S3 (via SNS)",
    emptyDraft: { topicArn: "", bucket: "" },
    Fields: SnsFields,
    buildConfig: (draft) => {
      const topicArn = (draft.topicArn ?? "").trim()
      if (!topicArn) {
        return "Enter the SNS topic ARN."
      }
      const bucket = (draft.bucket ?? "").trim()
      // The API checks the bucket and saves an ARN as its name.
      return bucket
        ? { kind: "aws_sns", topic_arn: topicArn, bucket }
        : { kind: "aws_sns", topic_arn: topicArn }
    },
    describeSource: (source) => source.config.topic_arn,
    sender: "SNS",
    Setup: SnsSetup,
  },
}

/** Providers in picker order. */
export const EVENT_SOURCE_PROVIDER_LIST: EventSourceProviderDefinition[] =
  Object.values(EVENT_SOURCE_PROVIDERS)

/**
 * The provider of a source's kind, or `undefined` for a kind this build does
 * not know (a newer API). Callers show the raw kind name then.
 */
export function getEventSourceProvider(
  kind: string
): EventSourceProviderDefinition | undefined {
  // Own keys only: an unknown kind like "toString" must not hit the prototype.
  if (!Object.prototype.hasOwnProperty.call(EVENT_SOURCE_PROVIDERS, kind)) {
    return undefined
  }
  return EVENT_SOURCE_PROVIDERS[kind as EventSourceKind]
}

/** Where a source's events come from, or its raw kind for an unknown kind. */
export function describeEventSource(source: EventSourceRead): string {
  return (
    getEventSourceProvider(source.kind)?.describeSource(source) ?? source.kind
  )
}
