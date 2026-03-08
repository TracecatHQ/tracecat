"use client"

import { Loader2 } from "lucide-react"
import type { AgentPresetVersionReadMinimal } from "@/client"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { cn } from "@/lib/utils"

const CURRENT_VERSION_VALUE = "__current__"

export function getAgentPresetVersionNumber(
  versions: AgentPresetVersionReadMinimal[] | undefined,
  versionId: string | null | undefined
): number | null {
  if (!versions || !versionId) {
    return null
  }
  return versions.find((version) => version.id === versionId)?.version ?? null
}

export function formatAgentPresetVersionLabel({
  currentVersionNumber,
  selectedVersionNumber,
  currentVersionId,
  selectedVersionId,
}: {
  currentVersionNumber?: number | null
  selectedVersionNumber?: number | null
  currentVersionId?: string | null
  selectedVersionId?: string | null
}): string {
  if (!selectedVersionId) {
    return currentVersionNumber
      ? `Current (v${currentVersionNumber})`
      : "Current"
  }
  if (selectedVersionId === currentVersionId) {
    return selectedVersionNumber
      ? `Pinned v${selectedVersionNumber} (current)`
      : "Pinned current version"
  }
  return selectedVersionNumber
    ? `Pinned v${selectedVersionNumber}`
    : "Pinned version"
}

interface AgentPresetVersionSelectProps {
  versions?: AgentPresetVersionReadMinimal[]
  versionsIsLoading: boolean
  versionsError: unknown
  selectedVersionId: string | null
  currentVersionId: string | null
  onSelect: (versionId: string | null) => void | Promise<void>
  disabled?: boolean
  placeholder?: string
  triggerClassName?: string
  allowCurrent?: boolean
}

export function AgentPresetVersionSelect({
  versions,
  versionsIsLoading,
  versionsError,
  selectedVersionId,
  currentVersionId,
  onSelect,
  disabled = false,
  placeholder = "Select version",
  triggerClassName,
  allowCurrent = true,
}: AgentPresetVersionSelectProps) {
  const value =
    selectedVersionId ?? (allowCurrent ? CURRENT_VERSION_VALUE : undefined)
  const currentVersionNumber = getAgentPresetVersionNumber(
    versions,
    currentVersionId
  )
  const selectedVersionNumber = getAgentPresetVersionNumber(
    versions,
    selectedVersionId
  )

  return (
    <Select
      value={value}
      onValueChange={(nextValue) =>
        void onSelect(nextValue === CURRENT_VERSION_VALUE ? null : nextValue)
      }
      disabled={disabled}
    >
      <SelectTrigger className={cn("min-w-36", triggerClassName)}>
        <SelectValue
          placeholder={placeholder}
          aria-label={formatAgentPresetVersionLabel({
            currentVersionNumber,
            selectedVersionNumber,
            currentVersionId,
            selectedVersionId,
          })}
        >
          {versionsIsLoading ? (
            <span className="flex items-center gap-2">
              <Loader2 className="size-3 animate-spin" />
              Loading...
            </span>
          ) : (
            formatAgentPresetVersionLabel({
              currentVersionNumber,
              selectedVersionNumber,
              currentVersionId,
              selectedVersionId,
            })
          )}
        </SelectValue>
      </SelectTrigger>
      <SelectContent>
        {versionsIsLoading ? (
          <SelectItem value="__loading" disabled>
            Loading versions...
          </SelectItem>
        ) : null}
        {versionsError ? (
          <SelectItem value="__error" disabled>
            Failed to load versions
          </SelectItem>
        ) : null}
        {!versionsIsLoading && !versionsError && allowCurrent ? (
          <SelectItem value={CURRENT_VERSION_VALUE}>
            {currentVersionNumber
              ? `Current (v${currentVersionNumber})`
              : "Current"}
          </SelectItem>
        ) : null}
        {!versionsIsLoading &&
        !versionsError &&
        (versions?.length ?? 0) === 0 ? (
          <SelectItem value="__empty" disabled>
            No versions found
          </SelectItem>
        ) : null}
        {versions?.map((version) => {
          const isCurrent = version.id === currentVersionId
          return (
            <SelectItem key={version.id} value={version.id}>
              {`v${version.version}${isCurrent ? " • Current" : ""}`}
            </SelectItem>
          )
        })}
      </SelectContent>
    </Select>
  )
}
