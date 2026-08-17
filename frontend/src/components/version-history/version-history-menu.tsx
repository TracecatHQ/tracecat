"use client"

import { History, Loader2 } from "lucide-react"
import type { ReactNode } from "react"
import { useState } from "react"
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import type {
  VersionedDocumentDescriptor,
  VersionHistoryEntry,
} from "@/components/version-history/types"

/** Props for {@link VersionHistoryMenu}. */
export type VersionHistoryMenuProps = {
  /** Document the versions belong to; drives dialog copy and current marker. */
  document: VersionedDocumentDescriptor
  /**
   * Renders the file tree and diff for the chosen version. Invoked only while
   * the dialog is open, inside a `key={versionId}` boundary, so the
   * implementation may own react-query hooks and local selection state.
   */
  renderVersionDiff: (versionId: string) => ReactNode
  /** Human-readable kind of document used in copy, e.g. `agent` or `skill`. */
  entityLabel: string
  /** Versions to list in the dropdown, newest first. */
  versions: VersionHistoryEntry[]
  /** True while the version list is being fetched. */
  isLoading: boolean
  /**
   * True when the version list failed to load. Renders an error notice in the
   * dropdown instead of the empty state, so a failed request never masquerades
   * as "no versions yet". The host owns any toast or retry affordance.
   */
  loadError?: boolean
  /**
   * Restores the given version. Resolving closes the dialog; rejecting keeps
   * it open so the host can surface the error and let the user retry.
   */
  onRestore: (versionId: string) => Promise<void>
  /** Disables the trigger button. */
  disabled?: boolean
  /**
   * Disables the confirm action in the restore dialog, e.g. when the host
   * cannot produce a draft snapshot to compare against. The dialog itself
   * stays open and dismissible.
   */
  restoreDisabled?: boolean
  /** Horizontal alignment of the dropdown relative to the trigger. */
  align?: "start" | "end"
}

/**
 * Document-agnostic version history surface: a clock trigger opening a
 * dropdown of versions, and a restore dialog that shows the selected version
 * diffed against the current draft via {@link VersionHistoryMenuProps.renderVersionDiff}.
 * All data fetching lives in the host; this shell owns only selection and
 * restore-pending state.
 */
export function VersionHistoryMenu({
  document: documentDescriptor,
  renderVersionDiff,
  entityLabel,
  versions,
  isLoading,
  loadError,
  onRestore,
  disabled,
  restoreDisabled,
  align = "end",
}: VersionHistoryMenuProps) {
  const [selectedVersion, setSelectedVersion] =
    useState<VersionHistoryEntry | null>(null)
  const [restorePending, setRestorePending] = useState(false)

  /**
   * Dropdown body: loading, load failure, empty, or the version list. An
   * if/else chain rather than chained ternaries, and ordered so the empty
   * state only ever shows on a genuinely successful empty result.
   */
  function renderVersionList(): ReactNode {
    if (isLoading) {
      return (
        <div className="flex items-center gap-2 px-3 py-3 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" />
          Loading versions…
        </div>
      )
    }
    if (loadError) {
      return (
        <div className="px-3 py-3 text-sm text-muted-foreground">
          Couldn't load version history.
        </div>
      )
    }
    if (versions.length === 0) {
      return (
        <div className="px-3 py-3 text-sm text-muted-foreground">
          No versions yet.
        </div>
      )
    }
    return (
      <ScrollArea className="max-h-80">
        <DropdownMenuGroup className="flex flex-col p-1">
          {versions.map((version) => {
            const isCurrent =
              version.isCurrent ??
              version.id === documentDescriptor.currentVersionId
            return (
              <DropdownMenuItem
                key={version.id}
                className="items-start px-3 py-2"
                disabled={restorePending}
                onSelect={() => setSelectedVersion(version)}
              >
                <div className="flex min-w-0 flex-col gap-0.5">
                  <div className="flex items-center gap-2">
                    <span className="font-medium">{version.label}</span>
                    {isCurrent ? (
                      <span className="text-xs text-muted-foreground">
                        Current
                      </span>
                    ) : null}
                  </div>
                  <div className="text-muted-foreground">
                    {version.description ??
                      new Date(version.createdAt).toLocaleString()}
                  </div>
                </div>
              </DropdownMenuItem>
            )
          })}
        </DropdownMenuGroup>
      </ScrollArea>
    )
  }

  async function handleConfirmRestore() {
    if (!selectedVersion) {
      return
    }
    setRestorePending(true)
    try {
      await onRestore(selectedVersion.id)
    } catch {
      return
    } finally {
      setRestorePending(false)
    }
    setSelectedVersion(null)
  }

  return (
    <>
      <DropdownMenu>
        <Tooltip>
          <TooltipTrigger asChild>
            <DropdownMenuTrigger asChild>
              <Button
                size="icon"
                variant="ghost"
                className="size-7"
                disabled={disabled}
              >
                <History className="size-4" />
                <span className="sr-only">Versions</span>
              </Button>
            </DropdownMenuTrigger>
          </TooltipTrigger>
          <TooltipContent>Versions</TooltipContent>
        </Tooltip>
        <DropdownMenuContent align={align} className="w-80 p-0">
          <div className="flex flex-col">
            <DropdownMenuLabel className="flex flex-col gap-1 px-3 py-2">
              <div className="text-xs font-medium">Version history</div>
              <div className="text-xs text-muted-foreground">
                Select a version of this {entityLabel} to review and restore.
              </div>
            </DropdownMenuLabel>
            <DropdownMenuSeparator className="mx-0 my-0" />
            {renderVersionList()}
          </div>
        </DropdownMenuContent>
      </DropdownMenu>

      <AlertDialog
        open={selectedVersion !== null}
        onOpenChange={(open) => {
          if (!open && !restorePending) {
            setSelectedVersion(null)
          }
        }}
      >
        <AlertDialogContent className="max-h-[85vh] max-w-5xl grid-rows-[auto_minmax(0,1fr)_auto] overflow-hidden">
          <AlertDialogHeader>
            <AlertDialogTitle>Restore version</AlertDialogTitle>
            <AlertDialogDescription>
              {selectedVersion
                ? `Compare ${selectedVersion.label} with the current draft of ${documentDescriptor.name}. Restoring replaces unsaved changes.`
                : "Restoring replaces unsaved changes."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          {selectedVersion ? (
            <div key={selectedVersion.id} className="min-h-0 overflow-hidden">
              {renderVersionDiff(selectedVersion.id)}
            </div>
          ) : null}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={restorePending}>
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={(event) => {
                event.preventDefault()
                void handleConfirmRestore()
              }}
              disabled={restorePending || restoreDisabled}
            >
              {restorePending ? (
                <>
                  <Loader2 className="mr-2 size-4 animate-spin" />
                  Restoring…
                </>
              ) : (
                "Restore version"
              )}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
