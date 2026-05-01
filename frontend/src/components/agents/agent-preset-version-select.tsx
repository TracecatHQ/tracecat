"use client"

import { ChevronDown, Loader2 } from "lucide-react"
import type { AgentPresetVersionReadMinimal } from "@/client"
import { Badge } from "@/components/ui/badge"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { cn } from "@/lib/utils"

const CURRENT_VERSION_VALUE = "__current__"

export function getAgentPresetVersionIdFromSelectValue(
  selectValue: string
): string | null {
  return selectValue === CURRENT_VERSION_VALUE ? null : selectValue
}

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
      ? `Version ${currentVersionNumber}, current`
      : "Current"
  }
  if (selectedVersionId === currentVersionId) {
    return selectedVersionNumber
      ? `Version ${selectedVersionNumber}, current`
      : "Current version"
  }
  return selectedVersionNumber ? `Version ${selectedVersionNumber}` : "Version"
}

export function getAgentPresetVersionFallbackLabel({
  currentVersionId,
  selectedVersionId,
}: {
  currentVersionId?: string | null
  selectedVersionId?: string | null
}): string {
  if (!selectedVersionId) {
    return "Current"
  }
  if (selectedVersionId === currentVersionId) {
    return "Current version"
  }
  return "Pinned version"
}

function VersionLabel({
  label,
  versionNumber,
  isCurrent = false,
  fallback = "Current",
}: {
  label?: string
  versionNumber?: number | null
  isCurrent?: boolean
  fallback?: string
}) {
  return (
    <span className="flex items-center gap-2">
      <span>
        {label ?? (versionNumber ? `Version ${versionNumber}` : fallback)}
      </span>
      {isCurrent ? (
        <Badge variant="secondary" className="h-5 px-1.5 text-[10px]">
          Current
        </Badge>
      ) : null}
    </span>
  )
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
        void onSelect(getAgentPresetVersionIdFromSelectValue(nextValue))
      }
      disabled={disabled}
    >
      <SelectTrigger
        icon={<ChevronDown className="ml-1 size-3" />}
        className={cn(
          "min-w-36 border-none bg-transparent px-2 text-xs font-medium shadow-none hover:bg-accent focus:ring-0",
          triggerClassName
        )}
      >
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
            <VersionLabel
              versionNumber={
                selectedVersionId ? selectedVersionNumber : currentVersionNumber
              }
              isCurrent={
                !selectedVersionId || selectedVersionId === currentVersionId
              }
              fallback={getAgentPresetVersionFallbackLabel({
                currentVersionId,
                selectedVersionId,
              })}
            />
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
            <VersionLabel label="Use current" />
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
              <VersionLabel
                versionNumber={version.version}
                isCurrent={isCurrent}
                fallback="Version"
              />
            </SelectItem>
          )
        })}
      </SelectContent>
    </Select>
  )
}
