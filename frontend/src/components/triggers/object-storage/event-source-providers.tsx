"use client"

import type { ComponentType } from "react"
import type {
  EventSourceCreate,
  EventSourceKind,
  EventSourceRead,
} from "@/client"
import { CopyableValue } from "@/components/triggers/object-storage/copyable-value"
import { Input } from "@/components/ui/input"

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
    </>
  )
}

function SnsSetup({ source }: ProviderSetupProps) {
  const subscribeCommand = source.endpoint_url
    ? `aws sns subscribe --topic-arn ${shellQuote(source.config.topic_arn)} --protocol https --notification-endpoint ${shellQuote(source.endpoint_url)}`
    : null
  return (
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
          Set a delivery retry policy that keeps retrying for 1 hour, so a short
          outage doesn't drop events.
        </li>
        <li>
          Add a dead-letter queue (SQS) to the subscription to keep the events
          SNS gives up on.
        </li>
      </ol>
      {subscribeCommand ? (
        <CopyableValue value={subscribeCommand} toastMessage="Command copied" />
      ) : null}
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
    emptyDraft: { topicArn: "" },
    Fields: SnsFields,
    buildConfig: (draft) => {
      const topicArn = (draft.topicArn ?? "").trim()
      if (!topicArn) {
        return "Enter the SNS topic ARN."
      }
      return { kind: "aws_sns", topic_arn: topicArn }
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
