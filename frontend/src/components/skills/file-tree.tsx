"use client"

import * as CollapsiblePrimitive from "@radix-ui/react-collapsible"
import { ChevronRight, Folder, FolderOpen } from "lucide-react"
import { useCallback, useRef, useState } from "react"
import { SkillFileIcon } from "@/components/skills/skill-file-icon"
import { Badge } from "@/components/ui/badge"
import type { SkillFileTreeNode } from "@/lib/skills-studio"
import { getAncestorFolderPaths } from "@/lib/skills-studio"
import { cn } from "@/lib/utils"

type SkillFileTreeProps = {
  nodes: SkillFileTreeNode[]
  selectedPath: string | null
  onSelectPath: (path: string) => void
}

/**
 * Collapsible skill file tree with folder grouping and file status badges.
 *
 * @param props Tree nodes and selection handlers.
 * @returns Rendered file tree.
 */
export function SkillFileTree({
  nodes,
  selectedPath,
  onSelectPath,
}: SkillFileTreeProps) {
  const [expandedFolders, setExpandedFolders] = useState<Set<string>>(
    () =>
      new Set(
        nodes.filter((node) => node.kind === "folder").map((node) => node.path)
      )
  )

  // Expand ancestor folders when selection changes, without an extra render cycle
  const prevSelectedPathRef = useRef(selectedPath)
  if (prevSelectedPathRef.current !== selectedPath) {
    prevSelectedPathRef.current = selectedPath
    if (selectedPath) {
      const ancestors = getAncestorFolderPaths(selectedPath)
      if (ancestors.length > 0) {
        setExpandedFolders((current) => {
          const next = new Set(current)
          for (const folderPath of ancestors) {
            next.add(folderPath)
          }
          return next
        })
      }
    }
  }

  const handleToggleFolder = useCallback((path: string) => {
    setExpandedFolders((current) => {
      const next = new Set(current)
      if (next.has(path)) {
        next.delete(path)
      } else {
        next.add(path)
      }
      return next
    })
  }, [])

  return (
    <div className="flex flex-col gap-1">
      {nodes.map((node) => (
        <SkillFileTreeNodeRow
          key={node.path}
          node={node}
          level={0}
          selectedPath={selectedPath}
          expandedFolders={expandedFolders}
          onToggleFolder={handleToggleFolder}
          onSelectPath={onSelectPath}
        />
      ))}
    </div>
  )
}

type SkillFileTreeNodeRowProps = {
  node: SkillFileTreeNode
  level: number
  selectedPath: string | null
  expandedFolders: Set<string>
  onToggleFolder: (path: string) => void
  onSelectPath: (path: string) => void
}

function SkillFileTreeNodeRow({
  node,
  level,
  selectedPath,
  expandedFolders,
  onToggleFolder,
  onSelectPath,
}: SkillFileTreeNodeRowProps) {
  const paddingLeft = 8 + level * 16

  if (node.kind === "folder") {
    const isExpanded = expandedFolders.has(node.path)

    return (
      <CollapsiblePrimitive.Root
        open={isExpanded}
        onOpenChange={() => onToggleFolder(node.path)}
      >
        <CollapsiblePrimitive.Trigger asChild>
          <button
            type="button"
            className="flex w-full items-center gap-2 rounded-md py-2 pr-2 text-left text-sm hover:bg-muted/50"
            style={{ paddingLeft }}
          >
            <ChevronRight
              className={cn(
                "size-4 shrink-0 text-muted-foreground transition-transform",
                isExpanded && "rotate-90"
              )}
            />
            {isExpanded ? (
              <FolderOpen className="size-4 shrink-0 text-muted-foreground" />
            ) : (
              <Folder className="size-4 shrink-0 text-muted-foreground" />
            )}
            <span className="min-w-0 flex-1 truncate" title={node.path}>
              {node.name}
            </span>
          </button>
        </CollapsiblePrimitive.Trigger>
        <CollapsiblePrimitive.Content className="flex flex-col gap-1 overflow-hidden">
          {node.children.map((child) => (
            <SkillFileTreeNodeRow
              key={child.path}
              node={child}
              level={level + 1}
              selectedPath={selectedPath}
              expandedFolders={expandedFolders}
              onToggleFolder={onToggleFolder}
              onSelectPath={onSelectPath}
            />
          ))}
        </CollapsiblePrimitive.Content>
      </CollapsiblePrimitive.Root>
    )
  }

  const isActive = node.path === selectedPath
  const isDeleted = node.file.change?.kind === "delete"
  const fileBadge =
    node.file.change?.kind === "delete"
      ? "Deleted"
      : node.file.change?.kind === "upload"
        ? "Replace"
        : node.file.change?.kind === "text"
          ? node.file.isNew
            ? "New"
            : "Edited"
          : null

  return (
    <button
      type="button"
      onClick={() => onSelectPath(node.path)}
      className={cn(
        "flex w-full items-center gap-2 rounded-md py-2 pr-2 text-left text-sm",
        isActive ? "bg-muted" : "hover:bg-muted/50",
        isDeleted && "opacity-60"
      )}
      style={{ paddingLeft }}
    >
      <SkillFileIcon path={node.path} />
      <span className="min-w-0 flex-1 truncate" title={node.path}>
        {node.name}
      </span>
      {fileBadge ? (
        <Badge variant="secondary" className="shrink-0">
          {fileBadge}
        </Badge>
      ) : null}
    </button>
  )
}
