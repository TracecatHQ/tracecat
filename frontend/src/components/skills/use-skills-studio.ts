"use client"

import { useRouter } from "next/navigation"
import {
  type ChangeEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import type {
  SkillDraftAttachUploadedBlobOp,
  SkillDraftDeleteFileOp,
  SkillDraftFileRead,
  SkillDraftMoveFileOp,
  SkillDraftRead,
  SkillDraftUpsertTextFileOp,
  SkillRead,
  SkillReadMinimal,
  SkillVersionRead,
} from "@/client"
import { toast } from "@/components/ui/use-toast"
import {
  useCreateSkillDraftUpload,
  useDeleteSkill,
  usePatchSkillDraft,
  usePublishSkill,
  useRestoreSkillVersion,
  useSkill,
  useSkillDraft,
  useSkillDraftFile,
  useSkillVersions,
} from "@/hooks/use-skills"
import { getApiErrorCode, getApiErrorDetail } from "@/lib/errors"
import {
  buildVisibleFiles,
  comparePaths,
  computeFileSha256,
  type DraftChange,
  getTextContentType,
  isEditablePath,
  isMarkdownPath,
  SKILL_MD_PATH,
  uploadFileToSession,
  type VisibleFileEntry,
  validateSkillDraftPath,
} from "@/lib/skills-studio"

type MoveSource = {
  fromPath: string
  isFolder: boolean
}

type RenameTarget = {
  fromPath: string
  isFolder: boolean
}

/** State and handlers backing the single-skill editor surface. */
type UseSkillsStudioReturn = {
  // Identity
  workspaceId: string
  skillId: string
  selectedPath: string | null

  // Delete skill
  showDeleteSkillDialog: boolean
  deleteSkillTarget: SkillReadMinimal | null
  deleteSkillPending: boolean
  onOpenDeleteSkillDialog: (skill: SkillReadMinimal) => void
  onDeleteSkillDialogChange: (open: boolean) => void
  onConfirmDeleteSkill: () => Promise<void>

  // Editor panel
  skill?: SkillRead
  skillLoading: boolean
  draft?: SkillDraftRead
  draftLoading: boolean
  visibleFiles: VisibleFileEntry[]
  selectedFile: VisibleFileEntry | null
  draftFile?: SkillDraftFileRead
  draftFileLoading: boolean
  currentTextValue: string | null
  markdownEditorActivatedRef: React.MutableRefObject<boolean>
  onSelectPath: (path: string) => void
  onEditorChange: (nextValue: string) => void
  onUndoSelectedFileChange: () => void
  onSaveWorkingCopy: () => Promise<void>
  onDeleteSelectedFile: () => void
  onReplaceSelectedFile: (event: ChangeEvent<HTMLInputElement>) => void

  // Inline create
  pendingCreate: boolean
  pendingCreateError: string | null
  onBeginCreate: () => void
  onSubmitCreate: (path: string) => void
  onCancelCreate: () => void
  onChangeCreatePath: (value: string) => void

  // Move
  moveSource: MoveSource | null
  onBeginMove: (path: string, isFolder: boolean) => void
  onCancelMove: () => void
  onCommitMove: (toFolderPath: string | null) => Promise<void>

  // Rename
  renameTarget: RenameTarget | null
  renameError: string | null
  onBeginRename: (path: string, isFolder: boolean) => void
  onCancelRename: () => void
  onSubmitRename: (newPath: string) => Promise<void>

  // Inspector / working copy
  hasUnsavedChanges: boolean
  canPublish: boolean
  saveWorkingCopyPending: boolean
  patchSkillDraftPending: boolean
  createSkillDraftUploadPending: boolean
  publishSkillPending: boolean
  onPublish: () => Promise<void>
  versions?: SkillVersionRead[]
  versionsLoading: boolean
  restoreSkillVersionPending: boolean
  onRestore: (versionId: string) => Promise<void>
}

type DraftChangesForSkill = Record<string, DraftChange>

const EMPTY_DRAFT_CHANGES: DraftChangesForSkill = {}

function remapDraftChangesAfterMove(
  changes: DraftChangesForSkill,
  ops: ReadonlyArray<SkillDraftMoveFileOp>
): DraftChangesForSkill {
  if (ops.length === 0) {
    return changes
  }
  const map = new Map<string, string>()
  for (const op of ops) {
    map.set(op.from_path, op.to_path)
  }
  let mutated = false
  const next: DraftChangesForSkill = {}
  for (const [path, change] of Object.entries(changes)) {
    const moved = map.get(path)
    if (moved && moved !== path) {
      next[moved] = change
      mutated = true
    } else {
      next[path] = change
    }
  }
  return mutated ? next : changes
}

function describeMoveError(error: unknown): string {
  switch (getApiErrorCode(error)) {
    case "move_target_exists":
      return "A file already exists at that path."
    case "move_source_not_found":
      return "The original file no longer exists."
    case "skill_md_immovable":
      return "SKILL.md must stay at the root of the skill."
    case "invalid_move":
      return "Source and destination must differ."
    default:
      return getApiErrorDetail(error) ?? "Failed to move file."
  }
}

/**
 * Encapsulates state, data-fetching, and handlers for the skills studio
 * editor pane scoped to a single skill.
 *
 * @param params.workspaceId Current workspace identifier.
 * @param params.skillId Active skill identifier.
 * @returns Everything the editor needs to render and interact.
 */
export function useSkillsStudio(params: {
  workspaceId: string
  skillId: string
}): UseSkillsStudioReturn {
  const { workspaceId, skillId } = params
  const router = useRouter()
  const markdownEditorActivatedRef = useRef(false)

  // ── State ──────────────────────────────────────────────────────────
  const [selectedPath, setSelectedPath] = useState<string | null>(null)
  const [draftChanges, setDraftChanges] =
    useState<DraftChangesForSkill>(EMPTY_DRAFT_CHANGES)
  const [pendingCreate, setPendingCreate] = useState(false)
  const [pendingCreateError, setPendingCreateError] = useState<string | null>(
    null
  )
  const [moveSource, setMoveSource] = useState<MoveSource | null>(null)
  const [renameTarget, setRenameTarget] = useState<RenameTarget | null>(null)
  const [renameError, setRenameError] = useState<string | null>(null)
  const [saveWorkingCopyPending, setSaveWorkingCopyPending] = useState(false)
  const saveWorkingCopyPendingRef = useRef(false)

  const updateDraftChanges = useCallback(
    (
      updater:
        | DraftChangesForSkill
        | ((current: DraftChangesForSkill) => DraftChangesForSkill)
    ) => {
      setDraftChanges((current) => {
        const next = typeof updater === "function" ? updater(current) : updater
        return next
      })
    },
    []
  )

  // ── Data fetching ──────────────────────────────────────────────────
  const { skill, skillLoading } = useSkill(workspaceId, skillId)
  const { draft, draftLoading } = useSkillDraft(workspaceId, skillId)
  const { versions, versionsLoading } = useSkillVersions(workspaceId, skillId)

  const visibleFiles = useMemo(
    () => buildVisibleFiles(draft?.files, draftChanges),
    [draft?.files, draftChanges]
  )
  const selectedFile =
    visibleFiles.find((file) => file.path === selectedPath) ?? null

  const selectedFileQueryPath = useMemo(() => {
    if (!selectedFile || selectedFile.change?.kind === "delete") {
      return null
    }
    if (selectedFile.isNew) {
      return null
    }
    return selectedFile.path
  }, [selectedFile])

  const { draftFile, draftFileLoading } = useSkillDraftFile(
    workspaceId,
    skillId,
    selectedFileQueryPath
  )

  const { patchSkillDraft, patchSkillDraftPending } =
    usePatchSkillDraft(workspaceId)
  const { createSkillDraftUpload, createSkillDraftUploadPending } =
    useCreateSkillDraftUpload(workspaceId)
  const { publishSkill, publishSkillPending } = usePublishSkill(workspaceId)
  const { restoreSkillVersion, restoreSkillVersionPending } =
    useRestoreSkillVersion(workspaceId)
  const { deleteSkill, deleteSkillPending } = useDeleteSkill(workspaceId)

  // Delete skill dialog state
  const [showDeleteSkillDialog, setShowDeleteSkillDialog] = useState(false)
  const [deleteSkillTarget, setDeleteSkillTarget] =
    useState<SkillReadMinimal | null>(null)

  // ── Derived ────────────────────────────────────────────────────────
  const hasUnsavedChanges = useMemo(
    () => Object.keys(draftChanges).length > 0,
    [draftChanges]
  )
  const canPublish = !hasUnsavedChanges && Boolean(draft?.is_publishable)

  const currentTextValue = useMemo(() => {
    if (!selectedFile) {
      return null
    }
    const pending = selectedFile.change
    if (pending?.kind === "text") {
      return pending.content
    }
    if (pending?.kind === "delete") {
      return null
    }
    if (selectedFile.isNew) {
      return ""
    }
    if (draftFile?.kind === "inline") {
      return draftFile.text_content ?? ""
    }
    return null
  }, [selectedFile, draftFile])

  // ── Effects ────────────────────────────────────────────────────────
  useEffect(() => {
    setSelectedPath(null)
    setDraftChanges(EMPTY_DRAFT_CHANGES)
    setPendingCreate(false)
    setPendingCreateError(null)
    setMoveSource(null)
    setRenameTarget(null)
    setRenameError(null)
    markdownEditorActivatedRef.current = false
  }, [skillId])

  useEffect(() => {
    markdownEditorActivatedRef.current = false
  }, [draft?.draft_revision, selectedPath])

  useEffect(() => {
    if (!selectedPath && visibleFiles[0]) {
      setSelectedPath(visibleFiles[0].path)
      return
    }
    if (
      selectedPath &&
      !visibleFiles.some((file) => file.path === selectedPath)
    ) {
      setSelectedPath(visibleFiles[0]?.path ?? null)
    }
  }, [selectedPath, visibleFiles])

  // ── Stable callbacks ────────────────────────────────────────────────
  const handleBeginCreate = useCallback(() => {
    setMoveSource(null)
    setPendingCreateError(null)
    setPendingCreate(true)
  }, [])
  const handleCancelCreate = useCallback(() => {
    setPendingCreate(false)
    setPendingCreateError(null)
  }, [])
  const handleChangeCreatePath = useCallback(() => {
    setPendingCreateError(null)
  }, [])
  const handleOpenDeleteSkillDialog = useCallback(
    (target: SkillReadMinimal) => {
      setDeleteSkillTarget(target)
      setShowDeleteSkillDialog(true)
    },
    []
  )

  // ── Handlers ───────────────────────────────────────────────────────
  const handleEditorChange = (nextValue: string) => {
    if (!selectedFile) {
      return
    }

    if (
      isMarkdownPath(selectedFile.path) &&
      !selectedFile.change &&
      !markdownEditorActivatedRef.current
    ) {
      return
    }

    updateDraftChanges((current) => {
      let serverText: string | null = null
      if (draftFile?.kind === "inline") {
        serverText = draftFile.text_content ?? ""
      }
      if (serverText !== null && nextValue === serverText) {
        const next = { ...current }
        delete next[selectedFile.path]
        return next
      }

      return {
        ...current,
        [selectedFile.path]: {
          kind: "text",
          content: nextValue,
          contentType:
            selectedFile.contentType || getTextContentType(selectedFile.path),
        },
      }
    })
  }

  const handleUndoSelectedFileChange = () => {
    if (!selectedFile) {
      return
    }
    updateDraftChanges((current) => {
      const next = { ...current }
      delete next[selectedFile.path]
      return next
    })
  }

  const handleDeleteSelectedFile = () => {
    if (!selectedFile) {
      return
    }
    updateDraftChanges((current) => {
      if (selectedFile.isNew) {
        const next = { ...current }
        delete next[selectedFile.path]
        return next
      }
      return {
        ...current,
        [selectedFile.path]: { kind: "delete" },
      }
    })
  }

  const handleReplaceSelectedFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!selectedFile || !file) {
      event.target.value = ""
      return
    }
    updateDraftChanges((current) => ({
      ...current,
      [selectedFile.path]: {
        kind: "upload",
        file,
        contentType: file.type || "application/octet-stream",
      },
    }))
    event.target.value = ""
  }

  const handleSubmitCreate = (rawPath: string) => {
    const path = rawPath.trim()
    if (!path) {
      setPendingCreate(false)
      setPendingCreateError(null)
      return
    }
    const pathError = validateSkillDraftPath(path)
    if (pathError) {
      setPendingCreateError(pathError)
      return
    }
    if (visibleFiles.some((file) => file.path === path)) {
      setPendingCreateError("A file at that path already exists.")
      return
    }
    if (!isEditablePath(path)) {
      setPendingCreateError(
        "Only text file types (.md, .py, .ts, .json, .yaml, …) can be created inline."
      )
      return
    }
    updateDraftChanges((current) => ({
      ...current,
      [path]: {
        kind: "text",
        content: "",
        contentType: getTextContentType(path),
      },
    }))
    setSelectedPath(path)
    setPendingCreate(false)
    setPendingCreateError(null)
  }

  const handleBeginMove = useCallback((path: string, isFolder: boolean) => {
    if (!isFolder && path === SKILL_MD_PATH) {
      return
    }
    setPendingCreate(false)
    setPendingCreateError(null)
    setMoveSource({ fromPath: path, isFolder })
  }, [])

  const handleCancelMove = useCallback(() => {
    setMoveSource(null)
  }, [])

  const handleCommitMove = async (toFolderPath: string | null) => {
    if (!moveSource || !draft) {
      return
    }
    const targetPrefix = toFolderPath ? `${toFolderPath}/` : ""
    const operations: SkillDraftMoveFileOp[] = []

    if (moveSource.isFolder) {
      const sourcePrefix = `${moveSource.fromPath}/`
      if (toFolderPath !== null && toFolderPath === moveSource.fromPath) {
        toast({
          title: "Move skipped",
          description: "Source and destination are the same.",
        })
        setMoveSource(null)
        return
      }
      if (
        toFolderPath !== null &&
        (toFolderPath === moveSource.fromPath ||
          toFolderPath.startsWith(sourcePrefix))
      ) {
        toast({
          title: "Invalid move",
          description: "Cannot move a folder into itself.",
          variant: "destructive",
        })
        return
      }
      const folderName = moveSource.fromPath.split("/").filter(Boolean).pop()
      if (!folderName) {
        setMoveSource(null)
        return
      }
      const movedFiles = visibleFiles.filter(
        (file) =>
          file.path === moveSource.fromPath ||
          file.path.startsWith(sourcePrefix)
      )
      for (const file of movedFiles) {
        const remainder = file.path.slice(sourcePrefix.length)
        const toPath = `${targetPrefix}${folderName}${remainder ? `/${remainder}` : ""}`
        if (toPath === file.path) {
          continue
        }
        operations.push({
          op: "move_file",
          from_path: file.path,
          to_path: toPath,
        })
      }
    } else {
      const fileName =
        moveSource.fromPath.split("/").pop() ?? moveSource.fromPath
      const toPath = `${targetPrefix}${fileName}`
      if (toPath === moveSource.fromPath) {
        setMoveSource(null)
        return
      }
      operations.push({
        op: "move_file",
        from_path: moveSource.fromPath,
        to_path: toPath,
      })
    }

    if (operations.length === 0) {
      setMoveSource(null)
      return
    }

    try {
      await patchSkillDraft({
        skillId,
        requestBody: {
          base_revision: draft.draft_revision,
          operations,
        },
      })
      updateDraftChanges((current) =>
        remapDraftChangesAfterMove(current, operations)
      )
      const movedSelection = operations.find(
        (op) => op.from_path === selectedPath
      )
      if (movedSelection) {
        setSelectedPath(movedSelection.to_path)
      }
      setMoveSource(null)
    } catch (error) {
      toast({
        title: "Move failed",
        description: describeMoveError(error),
      })
    }
  }

  const handleBeginRename = useCallback((path: string, isFolder: boolean) => {
    if (!isFolder && path === SKILL_MD_PATH) {
      return
    }
    setMoveSource(null)
    setPendingCreate(false)
    setRenameError(null)
    setRenameTarget({ fromPath: path, isFolder })
  }, [])

  const handleCancelRename = useCallback(() => {
    setRenameTarget(null)
    setRenameError(null)
  }, [])

  const handleSubmitRename = async (rawNewPath: string) => {
    if (!renameTarget || !draft) {
      return
    }
    const newPath = rawNewPath.trim()
    if (!newPath || newPath === renameTarget.fromPath) {
      setRenameTarget(null)
      setRenameError(null)
      return
    }
    const pathError = validateSkillDraftPath(newPath)
    if (pathError) {
      setRenameError(pathError)
      return
    }
    if (renameTarget.isFolder && newPath === SKILL_MD_PATH) {
      setRenameError("That path is reserved for the root SKILL.md file.")
      return
    }

    const operations: SkillDraftMoveFileOp[] = []
    if (renameTarget.isFolder) {
      const sourcePrefix = `${renameTarget.fromPath}/`
      if (
        newPath === renameTarget.fromPath ||
        newPath.startsWith(sourcePrefix)
      ) {
        setRenameError("Cannot rename a folder into itself.")
        return
      }
      const movedFiles = visibleFiles.filter(
        (file) =>
          file.path === renameTarget.fromPath ||
          file.path.startsWith(sourcePrefix)
      )
      for (const file of movedFiles) {
        const remainder = file.path.slice(renameTarget.fromPath.length + 1)
        const toPath = remainder ? `${newPath}/${remainder}` : newPath
        if (toPath === file.path) {
          continue
        }
        operations.push({
          op: "move_file",
          from_path: file.path,
          to_path: toPath,
        })
      }
    } else {
      if (!isEditablePath(newPath)) {
        setRenameError(
          "File extension must remain compatible (for example .md, .py, .ts, .json)."
        )
        return
      }
      operations.push({
        op: "move_file",
        from_path: renameTarget.fromPath,
        to_path: newPath,
      })
    }

    if (operations.length === 0) {
      setRenameTarget(null)
      setRenameError(null)
      return
    }

    try {
      await patchSkillDraft({
        skillId,
        requestBody: {
          base_revision: draft.draft_revision,
          operations,
        },
      })
      updateDraftChanges((current) =>
        remapDraftChangesAfterMove(current, operations)
      )
      const movedSelection = operations.find(
        (op) => op.from_path === selectedPath
      )
      if (movedSelection) {
        setSelectedPath(movedSelection.to_path)
      }
      setRenameTarget(null)
      setRenameError(null)
    } catch (error) {
      setRenameError(describeMoveError(error))
    }
  }

  const handleSaveWorkingCopy = async () => {
    if (!draft || saveWorkingCopyPendingRef.current) {
      return
    }

    if (Object.keys(draftChanges).length === 0) {
      return
    }

    saveWorkingCopyPendingRef.current = true
    setSaveWorkingCopyPending(true)

    try {
      const sortedChanges = Object.entries(draftChanges).sort(
        ([left], [right]) => comparePaths(left, right)
      )
      const operations: Array<
        | SkillDraftUpsertTextFileOp
        | SkillDraftAttachUploadedBlobOp
        | SkillDraftDeleteFileOp
      > = []

      for (const [path, change] of sortedChanges) {
        if (change.kind === "text") {
          operations.push({
            op: "upsert_text_file",
            path,
            content: change.content,
            content_type: change.contentType,
          })
          continue
        }

        if (change.kind === "delete") {
          operations.push({ op: "delete_file", path })
          continue
        }

        const sha256 = await computeFileSha256(change.file)
        const uploadSession = await createSkillDraftUpload({
          skillId,
          requestBody: {
            sha256,
            size_bytes: change.file.size,
            content_type: change.contentType,
          },
        })
        await uploadFileToSession(
          change.file,
          uploadSession.upload_url,
          uploadSession.method ?? "PUT",
          uploadSession.headers ?? {}
        )
        operations.push({
          op: "attach_uploaded_blob",
          path,
          upload_id: uploadSession.upload_id,
        })
      }

      await patchSkillDraft({
        skillId,
        requestBody: {
          base_revision: draft.draft_revision,
          operations,
        },
      })
      updateDraftChanges((current) => {
        const next = { ...current }
        for (const [path, change] of sortedChanges) {
          if (next[path] === change) {
            delete next[path]
          }
        }
        return next
      })
    } catch (error) {
      toast({
        title: "Save failed",
        description: getApiErrorDetail(error) ?? "Failed to save working copy.",
        variant: "destructive",
      })
    } finally {
      saveWorkingCopyPendingRef.current = false
      setSaveWorkingCopyPending(false)
    }
  }

  const handlePublish = async () => {
    try {
      await publishSkill({ skillId })
    } catch (error) {
      toast({
        title: "Publish failed",
        description: getApiErrorDetail(error) ?? "Failed to publish skill.",
        variant: "destructive",
      })
    }
  }

  const handleRestore = async (versionId: string) => {
    await restoreSkillVersion({ skillId, versionId })
  }

  const handleConfirmDeleteSkill = async () => {
    if (!deleteSkillTarget) {
      return
    }
    try {
      await deleteSkill({ skillId: deleteSkillTarget.id })
      setShowDeleteSkillDialog(false)
      setDeleteSkillTarget(null)
      router.push(`/workspaces/${workspaceId}/skills`)
    } catch {
      // The mutation hook already reports delete failures to the user.
    }
  }

  // ── Return ─────────────────────────────────────────────────────────
  return {
    workspaceId,
    skillId,
    selectedPath,

    showDeleteSkillDialog,
    deleteSkillTarget,
    deleteSkillPending,
    onOpenDeleteSkillDialog: handleOpenDeleteSkillDialog,
    onDeleteSkillDialogChange: setShowDeleteSkillDialog,
    onConfirmDeleteSkill: handleConfirmDeleteSkill,

    skill,
    skillLoading,
    draft,
    draftLoading,
    visibleFiles,
    selectedFile,
    draftFile,
    draftFileLoading,
    currentTextValue,
    markdownEditorActivatedRef,
    onSelectPath: setSelectedPath,
    onEditorChange: handleEditorChange,
    onUndoSelectedFileChange: handleUndoSelectedFileChange,
    onDeleteSelectedFile: handleDeleteSelectedFile,
    onReplaceSelectedFile: handleReplaceSelectedFile,
    onSaveWorkingCopy: handleSaveWorkingCopy,

    pendingCreate,
    pendingCreateError,
    onBeginCreate: handleBeginCreate,
    onSubmitCreate: handleSubmitCreate,
    onCancelCreate: handleCancelCreate,
    onChangeCreatePath: handleChangeCreatePath,

    moveSource,
    onBeginMove: handleBeginMove,
    onCancelMove: handleCancelMove,
    onCommitMove: handleCommitMove,

    renameTarget,
    renameError,
    onBeginRename: handleBeginRename,
    onCancelRename: handleCancelRename,
    onSubmitRename: handleSubmitRename,

    hasUnsavedChanges,
    canPublish,
    saveWorkingCopyPending,
    patchSkillDraftPending,
    createSkillDraftUploadPending,
    publishSkillPending,
    onPublish: handlePublish,
    versions,
    versionsLoading,
    restoreSkillVersionPending,
    onRestore: handleRestore,
  }
}
