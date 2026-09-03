"use client"

import { FolderInput, FolderRoot, PencilLine, X as XIcon } from "lucide-react"
import {
  type FormEvent,
  type KeyboardEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import {
  FileTree,
  FileTreeActions,
  FileTreeFile,
  FileTreeFolder,
  FileTreeName,
} from "@/components/ai-elements/file-tree"
import { SkillFileIcon } from "@/components/skills/skill-file-icon"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  getAncestorFolderPaths,
  type SkillFileTreeNode,
  type VisibleFileEntry,
} from "@/lib/skills-studio"
import { cn } from "@/lib/utils"

type MoveSource = {
  fromPath: string
  isFolder: boolean
}

type SkillFileTreeProps = {
  nodes: SkillFileTreeNode[]
  selectedPath: string | null
  onSelectPath: (path: string) => void
  pendingCreate?: boolean
  pendingCreateError?: string | null
  onSubmitCreate?: (path: string) => void
  onCancelCreate?: () => void
  onChangeCreatePath?: (value: string) => void
  moveSource?: MoveSource | null
  onCancelMove?: () => void
  onCommitMove?: (toFolderPath: string | null) => void
  onBeginRenameFolder?: (path: string) => void
}

/**
 * File tree for the skills editor built on AI Elements primitives.
 *
 * Adds an inline-create input row and a select-destination mode that
 * highlights folders as click targets when a move is in progress.
 */
export function SkillFileTree({
  nodes,
  selectedPath,
  onSelectPath,
  pendingCreate = false,
  pendingCreateError = null,
  onSubmitCreate,
  onCancelCreate,
  onChangeCreatePath,
  moveSource = null,
  onCancelMove,
  onCommitMove,
  onBeginRenameFolder,
}: SkillFileTreeProps) {
  const expanded = useExpandedFolders(nodes, selectedPath)
  const inMoveMode = moveSource !== null

  const handleSelect = useCallback(
    (path: string) => {
      if (inMoveMode) {
        return
      }
      onSelectPath(path)
    },
    [inMoveMode, onSelectPath]
  )

  return (
    <div className="flex flex-col">
      {inMoveMode && moveSource && onCancelMove ? (
        <MoveBanner moveSource={moveSource} onCancel={onCancelMove} />
      ) : null}

      <FileTree
        expanded={expanded.set}
        onExpandedChange={expanded.setSet}
        selectedPath={selectedPath ?? undefined}
        onSelect={handleSelect}
      >
        {pendingCreate && onSubmitCreate && onCancelCreate ? (
          <PendingCreateRow
            error={pendingCreateError}
            onSubmit={onSubmitCreate}
            onCancel={onCancelCreate}
            onChange={onChangeCreatePath}
          />
        ) : null}

        {inMoveMode && onCommitMove ? (
          <RootDropTarget
            onClick={() => onCommitMove(null)}
            label='Move to "/" (root)'
          />
        ) : null}

        {nodes.map((node) => (
          <SkillTreeNode
            key={node.path}
            node={node}
            moveSource={moveSource}
            onCommitMove={onCommitMove}
            onBeginRenameFolder={onBeginRenameFolder}
          />
        ))}
      </FileTree>
    </div>
  )
}

function useExpandedFolders(
  nodes: SkillFileTreeNode[],
  selectedPath: string | null
) {
  const [set, setSet] = useState<Set<string>>(
    () =>
      new Set(
        nodes.filter((node) => node.kind === "folder").map((node) => node.path)
      )
  )

  useEffect(() => {
    if (!selectedPath) {
      return
    }
    const ancestors = getAncestorFolderPaths(selectedPath)
    if (ancestors.length === 0) {
      return
    }
    setSet((current) => {
      const next = new Set(current)
      let mutated = false
      for (const folderPath of ancestors) {
        if (!next.has(folderPath)) {
          next.add(folderPath)
          mutated = true
        }
      }
      return mutated ? next : current
    })
  }, [selectedPath])

  return useMemo(() => ({ set, setSet }), [set])
}

type SkillTreeNodeProps = {
  node: SkillFileTreeNode
  moveSource: MoveSource | null
  onCommitMove?: (toFolderPath: string | null) => void
  onBeginRenameFolder?: (path: string) => void
}

function SkillTreeNode({
  node,
  moveSource,
  onCommitMove,
  onBeginRenameFolder,
}: SkillTreeNodeProps) {
  if (node.kind === "folder") {
    return (
      <FolderTreeNode
        node={node}
        moveSource={moveSource}
        onCommitMove={onCommitMove}
        onBeginRenameFolder={onBeginRenameFolder}
      />
    )
  }
  return <FileTreeNode node={node} moveSource={moveSource} />
}

type FolderTreeNodeProps = {
  node: Extract<SkillFileTreeNode, { kind: "folder" }>
  moveSource: MoveSource | null
  onCommitMove?: (toFolderPath: string | null) => void
  onBeginRenameFolder?: (path: string) => void
}

function FolderTreeNode({
  node,
  moveSource,
  onCommitMove,
  onBeginRenameFolder,
}: FolderTreeNodeProps) {
  const inMoveMode = moveSource !== null
  const isMoveSource = moveSource?.fromPath === node.path
  const isSelfDescendant = useMemo(
    () =>
      moveSource?.isFolder === true &&
      (node.path === moveSource.fromPath ||
        node.path.startsWith(`${moveSource.fromPath}/`)),
    [moveSource, node.path]
  )

  const handleClickAsTarget = useCallback(
    (event: React.MouseEvent) => {
      if (!inMoveMode || isSelfDescendant || !onCommitMove) {
        return
      }
      event.stopPropagation()
      onCommitMove(node.path)
    },
    [inMoveMode, isSelfDescendant, node.path, onCommitMove]
  )

  const renameAction =
    !inMoveMode && onBeginRenameFolder ? (
      <button
        type="button"
        onClick={(event) => {
          event.stopPropagation()
          onBeginRenameFolder(node.path)
        }}
        className="ml-auto flex size-5 shrink-0 items-center justify-center rounded text-muted-foreground opacity-0 transition-opacity hover:bg-background hover:text-foreground group-hover:opacity-100"
        aria-label={`Rename ${node.path}`}
      >
        <PencilLine className="size-3" />
      </button>
    ) : null

  return (
    <FileTreeFolder
      path={node.path}
      name={node.name}
      className={cn(
        inMoveMode &&
          !isSelfDescendant &&
          "cursor-pointer ring-1 ring-inset ring-transparent hover:ring-primary/40",
        isMoveSource && "opacity-60",
        isSelfDescendant && "pointer-events-none opacity-40"
      )}
      onClick={inMoveMode ? handleClickAsTarget : undefined}
      disableRowInteraction={inMoveMode}
      actions={renameAction}
    >
      {node.children.map((child) => (
        <SkillTreeNode
          key={child.path}
          node={child}
          moveSource={moveSource}
          onCommitMove={onCommitMove}
          onBeginRenameFolder={onBeginRenameFolder}
        />
      ))}
    </FileTreeFolder>
  )
}

type FileTreeNodeProps = {
  node: Extract<SkillFileTreeNode, { kind: "file" }>
  moveSource: MoveSource | null
}

function FileTreeNode({ node, moveSource }: FileTreeNodeProps) {
  const inMoveMode = moveSource !== null
  const isMoveSource = moveSource?.fromPath === node.path
  const fileBadge = computeFileBadge(node.file)

  return (
    <FileTreeFile
      path={node.path}
      name={node.name}
      className={cn(
        inMoveMode && "pointer-events-none",
        isMoveSource && "opacity-60",
        node.file.change?.kind === "delete" && "opacity-60 line-through"
      )}
    >
      <span className="size-3.5 shrink-0" />
      <SkillFileIcon
        path={node.path}
        className="size-4 text-muted-foreground"
      />
      <FileTreeName>{node.name}</FileTreeName>
      {fileBadge ? (
        <FileTreeActions>
          <Badge
            variant="secondary"
            className="h-4 shrink-0 px-1 text-[10px] font-light"
          >
            {fileBadge}
          </Badge>
        </FileTreeActions>
      ) : null}
    </FileTreeFile>
  )
}

function computeFileBadge(file: VisibleFileEntry): string | null {
  if (file.change?.kind === "delete") {
    return "Deleted"
  }
  if (file.change?.kind === "upload") {
    return "Replace"
  }
  if (file.change?.kind === "text") {
    return file.isNew ? "New" : "Edited"
  }
  return null
}

type MoveBannerProps = {
  moveSource: MoveSource
  onCancel: () => void
}

function MoveBanner({ moveSource, onCancel }: MoveBannerProps) {
  return (
    <div className="mb-2 flex items-center gap-2 rounded-md border border-dashed border-primary/40 bg-primary/5 px-2 py-1.5 text-xs">
      <FolderInput className="size-3.5 shrink-0 text-primary" />
      <span className="min-w-0 flex-1 truncate">
        Move{" "}
        <span className="font-medium">
          {moveSource.isFolder
            ? `${moveSource.fromPath}/`
            : moveSource.fromPath}
        </span>{" "}
        to… click a folder.
      </span>
      <Button
        size="icon"
        variant="ghost"
        className="size-5 shrink-0"
        onClick={onCancel}
        aria-label="Cancel move"
      >
        <XIcon className="size-3.5" />
      </Button>
    </div>
  )
}

type RootDropTargetProps = {
  onClick: () => void
  label: string
}

function RootDropTarget({ onClick, label }: RootDropTargetProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="mb-1 flex w-full items-center gap-2 rounded-md px-2 py-1 text-left text-xs text-muted-foreground ring-1 ring-inset ring-transparent transition-colors hover:bg-muted/50 hover:text-foreground hover:ring-primary/40"
    >
      <FolderRoot className="size-3.5 shrink-0" />
      {label}
    </button>
  )
}

type PendingCreateRowProps = {
  error: string | null
  onSubmit: (path: string) => void
  onCancel: () => void
  onChange?: (value: string) => void
}

function PendingCreateRow({
  error,
  onSubmit,
  onCancel,
  onChange,
}: PendingCreateRowProps) {
  const [value, setValue] = useState("")
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const trimmed = value.trim()
    if (!trimmed) {
      onCancel()
      return
    }
    onSubmit(trimmed)
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Escape") {
      event.preventDefault()
      onCancel()
    }
  }

  return (
    <form onSubmit={handleSubmit} className="mb-0.5 flex flex-col px-1">
      <div className="flex items-center gap-1.5 py-1">
        <span className="size-3.5 shrink-0" />
        <span className="size-4 shrink-0" />
        <Input
          ref={inputRef}
          value={value}
          onChange={(event) => {
            setValue(event.target.value)
            onChange?.(event.target.value)
          }}
          onKeyDown={handleKeyDown}
          onBlur={() => {
            if (!value.trim()) {
              onCancel()
            }
          }}
          placeholder="helpers/fetch.py"
          className="h-auto border-0 bg-transparent p-0 text-xs font-light shadow-none focus-visible:ring-0 focus-visible:ring-offset-0"
          aria-invalid={error !== null}
        />
      </div>
      {error ? (
        <div className="pl-[46px] pb-1 text-[11px] font-light text-destructive">
          {error}
        </div>
      ) : null}
    </form>
  )
}
