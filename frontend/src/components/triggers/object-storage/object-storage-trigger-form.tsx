"use client"

import { useState } from "react"
import { describeTriggerError } from "@/components/triggers/trigger-errors"
import type { TriggerKindFormProps } from "@/components/triggers/trigger-kind-types"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { ToggleTabs } from "@/components/ui/toggle-tabs"
import { useEventSources, useTriggerMutations } from "@/hooks/use-triggers"
import { useWorkspaceId } from "@/providers/workspace-id"

type SourceMode = "existing" | "new"

/**
 * Settings for a new object storage trigger: an SNS event source (picked or
 * created here) and a key prefix.
 */
export function ObjectStorageTriggerForm({
  formId,
  pending,
  onSubmit,
}: TriggerKindFormProps) {
  const workspaceId = useWorkspaceId()
  const { sources, isLoading: sourcesLoading } = useEventSources(workspaceId)
  const { createEventSource } = useTriggerMutations(workspaceId)
  // `null` until the user picks: no sources yet means a new one.
  const [chosenMode, setChosenMode] = useState<SourceMode | null>(null)
  const [sourceId, setSourceId] = useState("")
  const [sourceName, setSourceName] = useState("")
  const [topicArn, setTopicArn] = useState("")
  const [keyPrefix, setKeyPrefix] = useState("")
  const [formError, setFormError] = useState<string | null>(null)

  // Until the list loads, "no sources" isn't known; default to picking one.
  let mode: SourceMode = "existing"
  if (chosenMode) {
    mode = chosenMode
  } else if (!sourcesLoading && sources.length === 0) {
    mode = "new"
  }
  const busy = pending

  async function resolveSourceId(): Promise<string> {
    if (mode === "existing") {
      return sourceId
    }
    const created = await createEventSource.mutateAsync({
      name: sourceName.trim(),
      config: { kind: "aws_sns", topic_arn: topicArn.trim() },
      // A source starts nothing until a trigger reading it is online.
      status: "online",
    })
    // If the trigger itself fails, a retry reuses this source instead of
    // hitting the one-source-per-topic rule.
    setChosenMode("existing")
    setSourceId(created.id)
    setSourceName("")
    setTopicArn("")
    return created.id
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setFormError(null)
    if (mode === "existing" && !sourceId) {
      setFormError("Pick an event source.")
      return
    }
    try {
      await onSubmit(async () => ({
        kind: "object_storage",
        source_id: await resolveSourceId(),
        key_prefix: keyPrefix,
      }))
    } catch (error) {
      setFormError(describeTriggerError(error))
    }
  }

  return (
    <form id={formId} className="space-y-4" onSubmit={handleSubmit}>
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <Label className="text-xs">Event source</Label>
          <ToggleTabs<SourceMode>
            size="sm"
            value={mode}
            onValueChange={(next) => {
              setChosenMode(next)
              setFormError(null)
            }}
            options={[
              { value: "existing", content: "Existing" },
              { value: "new", content: "New SNS topic" },
            ]}
          />
        </div>
        {mode === "existing" ? (
          <Select
            value={sourceId}
            onValueChange={setSourceId}
            disabled={busy || sourcesLoading}
          >
            <SelectTrigger className="text-xs" aria-label="Event source">
              <SelectValue
                placeholder={
                  sources.length > 0
                    ? "Select an event source"
                    : "No event sources yet"
                }
              />
            </SelectTrigger>
            <SelectContent>
              {sources.map((source) => (
                <SelectItem
                  key={source.id}
                  value={source.id}
                  className="text-xs"
                >
                  <span>{source.name}</span>
                  <span className="ml-2 font-mono text-muted-foreground">
                    {source.config.topic_arn}
                  </span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        ) : (
          <div className="space-y-3">
            <Input
              aria-label="Event source name"
              className="text-xs"
              placeholder="Name, for example Uploads bucket"
              value={sourceName}
              required
              maxLength={255}
              disabled={busy}
              onChange={(event) => setSourceName(event.target.value)}
            />
            <Input
              aria-label="SNS topic ARN"
              className="font-mono text-xs"
              placeholder="arn:aws:sns:us-east-1:123456789012:bucket-events"
              value={topicArn}
              required
              disabled={busy}
              onChange={(event) => setTopicArn(event.target.value)}
            />
            <p className="text-xs text-muted-foreground">
              The standard SNS topic your bucket publishes object events to.
              FIFO topics can't deliver to HTTPS endpoints.
            </p>
          </div>
        )}
      </div>
      <div className="space-y-2">
        <Label htmlFor="object-storage-key-prefix" className="text-xs">
          Key prefix
        </Label>
        <Input
          id="object-storage-key-prefix"
          className="font-mono text-xs"
          placeholder="incoming/"
          value={keyPrefix}
          maxLength={1024}
          disabled={busy}
          onChange={(event) => setKeyPrefix(event.target.value)}
        />
        <p className="text-xs text-muted-foreground">
          Only new objects whose key starts with this start the targets. Leave
          empty to match every object.
        </p>
      </div>
      {formError ? (
        <p className="whitespace-pre-line text-xs text-destructive">
          {formError}
        </p>
      ) : null}
    </form>
  )
}
