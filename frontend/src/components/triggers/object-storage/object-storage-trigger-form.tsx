"use client"

import { useState } from "react"
import type { EventSourceKind } from "@/client"
import {
  describeEventSource,
  EVENT_SOURCE_PROVIDER_LIST,
  EVENT_SOURCE_PROVIDERS,
  type ProviderDraft,
} from "@/components/triggers/object-storage/event-source-providers"
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
 * Settings for a new object storage trigger: an event source (picked, or
 * created here after picking its storage provider) and a key prefix. Every
 * provider detail comes from `EVENT_SOURCE_PROVIDERS`.
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
  const [providerKind, setProviderKind] = useState<EventSourceKind>(
    EVENT_SOURCE_PROVIDER_LIST[0].kind
  )
  const provider = EVENT_SOURCE_PROVIDERS[providerKind]
  const [draft, setDraft] = useState<ProviderDraft>(provider.emptyDraft)
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
  let sourcePlaceholder = "Select an event source"
  if (sourcesLoading) {
    sourcePlaceholder = "Loading event sources…"
  } else if (sources.length === 0) {
    sourcePlaceholder = "No event sources yet"
  }

  async function resolveSourceId(): Promise<string> {
    if (mode === "existing") {
      return sourceId
    }
    const config = provider.buildConfig(draft)
    if (typeof config === "string") {
      throw new Error(config)
    }
    const created = await createEventSource.mutateAsync({
      name: sourceName.trim(),
      config,
      // A source starts nothing until a trigger reading it is online.
      status: "online",
    })
    // If the trigger itself fails, a retry reuses this source instead of
    // hitting the provider's one-source-per-feed rule.
    setChosenMode("existing")
    setSourceId(created.id)
    setSourceName("")
    setDraft(provider.emptyDraft)
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
            disabled={busy}
            value={mode}
            onValueChange={(next) => {
              setChosenMode(next)
              setFormError(null)
            }}
            options={[
              { value: "existing", content: "Existing" },
              { value: "new", content: "New source" },
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
              <SelectValue placeholder={sourcePlaceholder} />
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
                    {describeEventSource(source)}
                  </span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        ) : (
          <div className="space-y-3">
            <Select
              value={providerKind}
              onValueChange={(next) => {
                const kind = next as EventSourceKind
                setProviderKind(kind)
                setDraft(EVENT_SOURCE_PROVIDERS[kind].emptyDraft)
              }}
              disabled={busy}
            >
              <SelectTrigger className="text-xs" aria-label="Provider">
                <SelectValue placeholder="Select a provider" />
              </SelectTrigger>
              <SelectContent>
                {EVENT_SOURCE_PROVIDER_LIST.map((option) => (
                  <SelectItem
                    key={option.kind}
                    value={option.kind}
                    className="text-xs"
                  >
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
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
            <provider.Fields
              draft={draft}
              onDraftChange={setDraft}
              disabled={busy}
            />
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
