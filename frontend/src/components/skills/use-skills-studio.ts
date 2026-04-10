"use client"

import { useRouter } from "next/navigation"
import {
  type ChangeEvent,
  type DragEvent,
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
  SkillDraftRead,
  SkillDraftUpsertTextFileOp,
  SkillRead,
  SkillUpload,
  SkillVersionRead,
} from "@/client"
import { toast } from "@/components/ui/use-toast"
import {
  useCreateSkill,
  useCreateSkillDraftUpload,
  usePatchSkillDraft,
  usePublishSkill,
  useRestoreSkillVersion,
  useSkill,
  useSkillDraft,
  useSkillDraftFile,
  useSkills,
  useSkillVersions,
  useUploadSkill,
} from "@/hooks/use-skills"
import type { TracecatApiError } from "@/lib/errors"
import { getApiErrorDetail } from "@/lib/errors"
import {
  buildVisibleFiles,
  comparePaths,
  computeFileSha256,
  type DraftChange,
  extractDroppedFiles,
  fileToUploadEntry,
  getTextContentType,
  getUploadRootName,
  isEditablePath,
  isMarkdownPath,
  uploadFileToSession,
  type VisibleFileEntry,
} from "@/lib/skills-studio"
import { slugify } from "@/lib/utils"

/** All state, data, and handlers for the skills studio. */
type UseSkillsStudioReturn = {
  // Navigation / selection
  workspaceId: string
  selectedSkillId: string | null
  selectedPath: string | null

  // Skill list panel
  search: string
  onSearchChange: (value: string) => void
  visibleSkills: SkillRead[]
  skillsLoading: boolean
  skillsError: TracecatApiError | null
  onSelectSkill: (skillId: string) => void
  onOpenNewSkillDialog: () => void
  onOpenUploadSkillDialog: () => void

  // Upload skill dialog
  showUploadSkillDialog: boolean
  onUploadSkillDialogChange: (open: boolean) => void
  isDragOver: boolean
  onDragOver: (event: DragEvent<HTMLDivElement>) => void
  onDragLeave: () => void
  onDrop: (event: DragEvent<HTMLDivElement>) => void
  onDirectoryInput: (event: ChangeEvent<HTMLInputElement>) => void
  uploadSkillPending: boolean

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
  onDeleteSelectedFile: () => void
  onUndoSelectedFileChange: () => void
  onReplaceFile: (event: ChangeEvent<HTMLInputElement>) => void
  onSaveWorkingCopy: () => Promise<void>
  onOpenNewFileDialog: () => void

  // Inspector panel
  hasUnsavedChanges: boolean
  canPublish: boolean
  patchSkillDraftPending: boolean
  createSkillDraftUploadPending: boolean
  publishSkillPending: boolean
  onPublish: () => Promise<void>
  versions?: SkillVersionRead[]
  versionsLoading: boolean
  restoreSkillVersionPending: boolean
  onRestore: (versionId: string) => Promise<void>

  // Create skill dialog
  showNewSkillDialog: boolean
  onNewSkillDialogChange: (open: boolean) => void
  newSkillTitle: string
  onNewSkillTitleChange: (value: string) => void
  newSkillSlug: string
  onNewSkillSlugChange: (value: string) => void
  newSkillDescription: string
  onNewSkillDescriptionChange: (value: string) => void
  createSkillPending: boolean
  onCreateSkill: () => Promise<void>

  // Add file dialog
  showNewFileDialog: boolean
  onNewFileDialogChange: (open: boolean) => void
  newFilePath: string
  onNewFilePathChange: (value: string) => void
  onCreateNewFile: () => void
}

/**
 * Encapsulates all state, data-fetching, and handlers for the skills studio.
 *
 * @param params.workspaceId Current workspace identifier.
 * @param params.initialSkillId Optional skill to select on mount.
 * @returns Everything the studio panels need to render and interact.
 */
export function useSkillsStudio(params: {
  workspaceId: string
  initialSkillId?: string
}): UseSkillsStudioReturn {
  const { workspaceId, initialSkillId } = params
  const router = useRouter()
  const markdownEditorActivatedRef = useRef(false)

  // ── State ──────────────────────────────────────────────────────────
  const [search, setSearch] = useState("")
  const [selectedSkillId, setSelectedSkillId] = useState<string | null>(
    initialSkillId ?? null
  )
  const [selectedPath, setSelectedPath] = useState<string | null>(null)
  const [draftChanges, setDraftChanges] = useState<Record<string, DraftChange>>(
    {}
  )
  const [showNewSkillDialog, setShowNewSkillDialog] = useState(false)
  const [showUploadSkillDialog, setShowUploadSkillDialog] = useState(false)
  const [showNewFileDialog, setShowNewFileDialog] = useState(false)
  const [newSkillSlug, setNewSkillSlug] = useState("")
  const [newSkillTitle, setNewSkillTitle] = useState("")
  const [newSkillDescription, setNewSkillDescription] = useState("")
  const [newFilePath, setNewFilePath] = useState("")
  const [isDragOver, setIsDragOver] = useState(false)

  // ── Data fetching ──────────────────────────────────────────────────
  const { skills, skillsLoading, skillsError } = useSkills(workspaceId)

  const { skill, skillLoading } = useSkill(workspaceId, selectedSkillId)
  const { draft, draftLoading } = useSkillDraft(workspaceId, selectedSkillId)
  const { versions, versionsLoading } = useSkillVersions(
    workspaceId,
    selectedSkillId
  )

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
    selectedSkillId,
    selectedFileQueryPath
  )

  const { createSkill, createSkillPending } = useCreateSkill(workspaceId)
  const { uploadSkill, uploadSkillPending } = useUploadSkill(workspaceId)
  const { patchSkillDraft, patchSkillDraftPending } =
    usePatchSkillDraft(workspaceId)
  const { createSkillDraftUpload, createSkillDraftUploadPending } =
    useCreateSkillDraftUpload(workspaceId)
  const { publishSkill, publishSkillPending } = usePublishSkill(workspaceId)
  const { restoreSkillVersion, restoreSkillVersionPending } =
    useRestoreSkillVersion(workspaceId)

  // ── Derived ────────────────────────────────────────────────────────
  const visibleSkills = useMemo(() => {
    const query = search.trim().toLowerCase()
    if (!query) {
      return skills ?? []
    }
    return (skills ?? []).filter((s) => {
      return (
        s.slug.toLowerCase().includes(query) ||
        (s.title ?? "").toLowerCase().includes(query) ||
        (s.description ?? "").toLowerCase().includes(query)
      )
    })
  }, [search, skills])

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
    setSelectedSkillId(initialSkillId ?? null)
  }, [initialSkillId])

  useEffect(() => {
    setDraftChanges({})
    setSelectedPath(null)
    markdownEditorActivatedRef.current = false
  }, [selectedSkillId])

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
  const handleDragOver = useCallback((event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    setIsDragOver(true)
  }, [])
  const handleDragLeave = useCallback(() => setIsDragOver(false), [])
  const handleOpenNewSkillDialog = useCallback(
    () => setShowNewSkillDialog(true),
    []
  )
  const handleOpenUploadSkillDialog = useCallback(
    () => setShowUploadSkillDialog(true),
    []
  )
  const handleOpenNewFileDialog = useCallback(
    () => setShowNewFileDialog(true),
    []
  )

  // ── Handlers ───────────────────────────────────────────────────────
  const handleSelectSkill = (skillId: string) => {
    setSelectedSkillId(skillId)
    router.push(`/workspaces/${workspaceId}/skills/${skillId}`)
  }

  const handleCreateSkill = async () => {
    const slug =
      slugify(newSkillSlug.trim() || newSkillTitle.trim(), "-") || "skill"
    const created = await createSkill({
      slug,
      title: newSkillTitle.trim() || null,
      description: newSkillDescription.trim() || null,
    })
    setShowNewSkillDialog(false)
    setNewSkillSlug("")
    setNewSkillTitle("")
    setNewSkillDescription("")
    router.push(`/workspaces/${workspaceId}/skills/${created.id}`)
  }

  const handleUploadPayload = async (payload: SkillUpload) => {
    const created = await uploadSkill(payload)
    router.push(`/workspaces/${workspaceId}/skills/${created.id}`)
  }

  const handleUploadFiles = async (
    files: Array<{ file: File; path: string }>
  ) => {
    if (files.length === 0) {
      return
    }

    const paths = files.map(({ path }) => path.replace(/^\/+/, ""))
    const rootName = getUploadRootName(paths)
    const normalizedFiles = await Promise.all(
      files.map(async ({ file, path }) => {
        const normalizedPath = path.replace(/^\/+/, "")
        const relativePath = rootName
          ? normalizedPath.slice(rootName.length + 1)
          : normalizedPath
        return await fileToUploadEntry(file, relativePath || file.name)
      })
    )
    const slug = slugify(
      rootName ?? files[0]?.file.name.replace(/\.[^.]+$/, ""),
      "-"
    )
    await handleUploadPayload({
      slug: slug || "skill",
      files: normalizedFiles,
    })
  }

  const handleDirectoryInput = async (event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? [])
    if (files.length === 0) {
      return
    }
    try {
      await handleUploadFiles(
        files.map((file) => ({
          file,
          path: file.webkitRelativePath || file.name,
        }))
      )
      setShowUploadSkillDialog(false)
    } catch (error) {
      toast({
        title: "Upload failed",
        description: getApiErrorDetail(error) ?? "Failed to upload skill.",
        variant: "destructive",
      })
    } finally {
      event.target.value = ""
    }
  }

  const handleDrop = async (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    setIsDragOver(false)
    try {
      const droppedFiles = await extractDroppedFiles(event)
      await handleUploadFiles(droppedFiles)
      setShowUploadSkillDialog(false)
    } catch (error) {
      toast({
        title: "Drop upload failed",
        description:
          getApiErrorDetail(error) ?? "Failed to upload dropped directory.",
        variant: "destructive",
      })
    }
  }

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

    setDraftChanges((current) => {
      let serverText: string | null = null
      if (selectedFile.isNew) {
        serverText = ""
      } else if (draftFile?.kind === "inline") {
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

  const handleDeleteSelectedFile = () => {
    if (!selectedFile) {
      return
    }
    setDraftChanges((current) => ({
      ...current,
      [selectedFile.path]: { kind: "delete" },
    }))
  }

  const handleUndoSelectedFileChange = () => {
    if (!selectedFile) {
      return
    }
    setDraftChanges((current) => {
      const next = { ...current }
      delete next[selectedFile.path]
      return next
    })
  }

  const handleCreateNewFile = () => {
    const path = newFilePath.trim()
    if (!path) {
      return
    }
    if (!isEditablePath(path)) {
      toast({
        title: "Unsupported file type",
        description: "Only .md and .py files can be created inline.",
        variant: "destructive",
      })
      return
    }
    setDraftChanges((current) => ({
      ...current,
      [path]: {
        kind: "text",
        content: "",
        contentType: getTextContentType(path),
      },
    }))
    setSelectedPath(path)
    setNewFilePath("")
    setShowNewFileDialog(false)
  }

  const handleReplaceFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!selectedFile || !file) {
      return
    }
    setDraftChanges((current) => ({
      ...current,
      [selectedFile.path]: {
        kind: "upload",
        file,
        contentType: file.type || "application/octet-stream",
      },
    }))
    event.target.value = ""
  }

  const handleSaveWorkingCopy = async () => {
    if (!draft || !selectedSkillId) {
      return
    }

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
          skillId: selectedSkillId,
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
        skillId: selectedSkillId,
        requestBody: {
          base_revision: draft.draft_revision,
          operations,
        },
      })
      setDraftChanges({})
    } catch (error) {
      toast({
        title: "Save failed",
        description: getApiErrorDetail(error) ?? "Failed to save working copy.",
        variant: "destructive",
      })
    }
  }

  const handlePublish = async () => {
    if (!selectedSkillId) {
      return
    }
    try {
      await publishSkill({ skillId: selectedSkillId })
    } catch (error) {
      toast({
        title: "Publish failed",
        description: getApiErrorDetail(error) ?? "Failed to publish skill.",
        variant: "destructive",
      })
    }
  }

  const handleRestore = async (versionId: string) => {
    if (!selectedSkillId) {
      return
    }
    await restoreSkillVersion({ skillId: selectedSkillId, versionId })
    setDraftChanges({})
  }

  // ── Return ─────────────────────────────────────────────────────────
  return {
    workspaceId,
    selectedSkillId,
    selectedPath,

    search,
    onSearchChange: setSearch,
    visibleSkills,
    skillsLoading,
    skillsError: skillsError ?? null,
    onSelectSkill: handleSelectSkill,
    onOpenNewSkillDialog: handleOpenNewSkillDialog,
    onOpenUploadSkillDialog: handleOpenUploadSkillDialog,

    showUploadSkillDialog,
    onUploadSkillDialogChange: setShowUploadSkillDialog,
    isDragOver,
    onDragOver: handleDragOver,
    onDragLeave: handleDragLeave,
    onDrop: handleDrop,
    onDirectoryInput: handleDirectoryInput,
    uploadSkillPending,

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
    onDeleteSelectedFile: handleDeleteSelectedFile,
    onUndoSelectedFileChange: handleUndoSelectedFileChange,
    onReplaceFile: handleReplaceFile,
    onSaveWorkingCopy: handleSaveWorkingCopy,
    onOpenNewFileDialog: handleOpenNewFileDialog,

    hasUnsavedChanges,
    canPublish,
    patchSkillDraftPending,
    createSkillDraftUploadPending,
    publishSkillPending,
    onPublish: handlePublish,
    versions,
    versionsLoading,
    restoreSkillVersionPending,
    onRestore: handleRestore,

    showNewSkillDialog,
    onNewSkillDialogChange: setShowNewSkillDialog,
    newSkillTitle,
    onNewSkillTitleChange: setNewSkillTitle,
    newSkillSlug,
    onNewSkillSlugChange: setNewSkillSlug,
    newSkillDescription,
    onNewSkillDescriptionChange: setNewSkillDescription,
    createSkillPending,
    onCreateSkill: handleCreateSkill,

    showNewFileDialog,
    onNewFileDialogChange: setShowNewFileDialog,
    newFilePath,
    onNewFilePathChange: setNewFilePath,
    onCreateNewFile: handleCreateNewFile,
  }
}
