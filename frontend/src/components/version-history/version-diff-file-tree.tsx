"use client"

import { FileIcon } from "lucide-react"
import type { ReactNode } from "react"
import { useMemo } from "react"
import {
  FileTree,
  FileTreeFile,
  FileTreeFolder,
  FileTreeIcon,
  FileTreeName,
} from "@/components/ai-elements/file-tree"
import type {
  VersionFileEntry,
  VersionFileStatus,
} from "@/components/version-history/types"
import type { FileTreeNode } from "@/lib/file-tree"
import { buildFileTree } from "@/lib/file-tree"
import { cn } from "@/lib/utils"

/** Badge copy per file status. `null` means no badge is rendered. */
const STATUS_LABELS: Record<VersionFileStatus, string | null> = {
  added: "Added",
  removed: "Removed",
  modified: "Modified",
  unchanged: null,
}

/** Props for {@link VersionDiffFileTree}. */
export type VersionDiffFileTreeProps = {
  /** Files to display, each labelled with what restoring would do to it. */
  files: readonly VersionFileEntry[]
  /** Path of the file whose diff is shown, or null when nothing is selected. */
  selectedPath: string | null
  /** Called when the user picks a file row. */
  onSelectPath: (path: string) => void
  /**
   * Exact paths to sort first within their level, e.g. a well-known entrypoint
   * such as `SKILL.md`.
   */
  pinnedPaths?: readonly string[]
  /** Extra classes for the tree container. */
  className?: string
}

/**
 * Collects every folder path in the tree so the tree can start fully expanded.
 */
function collectFolderPaths(
  nodes: FileTreeNode<VersionFileEntry>[],
  into: Set<string>
): Set<string> {
  for (const node of nodes) {
    if (node.kind === "folder") {
      into.add(node.path)
      collectFolderPaths(node.children, into)
    }
  }
  return into
}

/**
 * Renders one tree node. Files carry a quiet status badge; rows whose status is
 * `removed` are dimmed with a struck-through name.
 */
function renderNode(node: FileTreeNode<VersionFileEntry>): ReactNode {
  if (node.kind === "folder") {
    return (
      <FileTreeFolder key={node.path} name={node.name} path={node.path}>
        {node.children.map(renderNode)}
      </FileTreeFolder>
    )
  }
  const statusLabel = STATUS_LABELS[node.file.status]
  const isRemoved = node.file.status === "removed"
  return (
    <FileTreeFile
      key={node.path}
      name={node.name}
      path={node.path}
      className={cn(isRemoved && "opacity-60")}
    >
      <span className="size-3.5 shrink-0" />
      <FileTreeIcon>
        <FileIcon className="size-4 text-muted-foreground" />
      </FileTreeIcon>
      <FileTreeName className={cn(isRemoved && "line-through")}>
        {node.name}
      </FileTreeName>
      {statusLabel ? (
        <span className="ml-auto shrink-0 rounded-sm border border-border px-1 text-[10px] leading-4 text-muted-foreground">
          {statusLabel}
        </span>
      ) : null}
    </FileTreeFile>
  )
}

/**
 * Read-only file tree for the version diff dialog. Shows every path touched by
 * restoring a version with a neutral `Added` / `Removed` / `Modified` badge and
 * no badge for unchanged files. Selection drives which diff is shown; there is
 * no rename, move, or create behavior.
 */
export function VersionDiffFileTree({
  files,
  selectedPath,
  onSelectPath,
  pinnedPaths,
  className,
}: VersionDiffFileTreeProps) {
  const nodes = useMemo(
    () => buildFileTree(files, { pinnedPaths }),
    [files, pinnedPaths]
  )
  const defaultExpanded = useMemo(
    () => collectFolderPaths(nodes, new Set<string>()),
    [nodes]
  )

  return (
    <FileTree
      className={className}
      defaultExpanded={defaultExpanded}
      selectedPath={selectedPath ?? undefined}
      onSelect={onSelectPath}
    >
      {nodes.map(renderNode)}
    </FileTree>
  )
}
