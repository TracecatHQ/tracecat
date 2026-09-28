"use client"

import { Loader2 } from "lucide-react"
import { InlineDiffView } from "@/components/diff/inline-diff-view"
import { ScrollArea } from "@/components/ui/scroll-area"
import type {
  VersionFileDiffState,
  VersionFileEntry,
} from "@/components/version-history/types"
import { VersionDiffFileTree } from "@/components/version-history/version-diff-file-tree"

/** Props for {@link VersionDiffBody}. */
export type VersionDiffBodyProps = {
  /** Files touched by restoring the version, shown in the left-hand tree. */
  files: readonly VersionFileEntry[]
  /** Path selected in the tree, or null when nothing is selected yet. */
  selectedPath: string | null
  /** Called when the user picks a file in the tree. */
  onSelectPath: (path: string) => void
  /**
   * Diff payload for the selected file. Pass null while content is loading;
   * a spinner is shown until the payload's path matches {@link selectedPath}.
   */
  diff: VersionFileDiffState | null
  /** Left-hand side of the diff header, e.g. `Current draft`. */
  draftLabel: string
  /** Right-hand side of the diff header, e.g. `v3`. */
  versionLabel: string
  /**
   * When set, replaces the whole body with this notice — used when the host
   * cannot produce a draft snapshot to diff against.
   */
  message?: string
  /** Exact paths to pin to the top of the file tree, in order. */
  pinnedPaths?: readonly string[]
}

/**
 * Two-column body for the version diff dialog: a read-only file tree on the
 * left and an inline diff of the selected file on the right, headed by a
 * `{draftLabel} → {versionLabel}` direction label.
 */
export function VersionDiffBody({
  files,
  selectedPath,
  onSelectPath,
  diff,
  draftLabel,
  versionLabel,
  message,
  pinnedPaths,
}: VersionDiffBodyProps) {
  if (message) {
    return (
      <div className="flex min-h-0 items-center justify-center rounded-md border px-4 py-8 text-sm text-muted-foreground">
        {message}
      </div>
    )
  }

  let diffContent: React.ReactNode
  if (!selectedPath) {
    diffContent = (
      <div className="flex h-full items-center justify-center px-4 text-sm text-muted-foreground">
        Select a file to compare.
      </div>
    )
  } else if (diff && diff.path === selectedPath) {
    diffContent = (
      <InlineDiffView
        path={diff.path}
        oldValue={diff.oldValue}
        newValue={diff.newValue}
        contentType={diff.contentType}
        downloadUrl={diff.downloadUrl}
      />
    )
  } else {
    diffContent = (
      <div className="flex h-full items-center justify-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" />
        Loading diff…
      </div>
    )
  }

  return (
    <div className="grid min-h-0 gap-3 md:h-[min(32rem,50vh)] md:grid-cols-[minmax(0,240px)_minmax(0,1fr)]">
      <ScrollArea className="min-h-0 rounded-md border p-2">
        <VersionDiffFileTree
          files={files}
          selectedPath={selectedPath}
          onSelectPath={onSelectPath}
          pinnedPaths={pinnedPaths}
        />
      </ScrollArea>
      <div className="flex min-h-0 min-w-0 overflow-hidden rounded-md border">
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          <div className="flex items-center justify-between gap-2 border-b px-3 py-2">
            <div className="truncate text-xs font-medium">
              {selectedPath ?? "Select a file"}
            </div>
            <div className="shrink-0 text-xs text-muted-foreground">
              {`${draftLabel} → ${versionLabel}`}
            </div>
          </div>
          <div className="min-h-0 min-w-0 flex-1 overflow-auto">
            {diffContent}
          </div>
        </div>
      </div>
    </div>
  )
}
