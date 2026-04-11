import { act, renderHook, waitFor } from "@testing-library/react"
import type { ChangeEvent } from "react"
import type { SkillDraftRead, SkillRead } from "@/client"
import { useSkillsStudio } from "@/components/skills/use-skills-studio"
import {
  composeMarkdownFrontmatter,
  splitMarkdownFrontmatter,
} from "@/lib/markdown-frontmatter"
import { getLanguageForPath, uploadFileToSession } from "@/lib/skills-studio"

const mockRouterPush = jest.fn()
const mockCreateSkill = jest.fn()
const mockUploadSkill = jest.fn()
const mockPatchSkillDraft = jest.fn()
const mockCreateSkillDraftUpload = jest.fn()
const mockPublishSkill = jest.fn()
const mockRestoreSkillVersion = jest.fn()

jest.mock("@/lib/skills-studio", () => {
  const actual = jest.requireActual("@/lib/skills-studio")
  return {
    ...actual,
    computeFileSha256: jest.fn(async () => "sha-upload"),
    uploadFileToSession: jest.fn(),
  }
})

const mockSkill: SkillRead = {
  id: "skill-1",
  workspace_id: "workspace-1",
  slug: "skill-1",
  title: "Skill 1",
  description: null,
  current_version_id: null,
  draft_revision: 1,
  created_at: "2026-04-10T00:00:00.000Z",
  updated_at: "2026-04-10T00:00:00.000Z",
  archived_at: null,
  current_version: null,
  is_draft_publishable: true,
  draft_validation_errors: [],
  draft_file_count: 1,
}

const mockDraft: SkillDraftRead = {
  skill_id: "skill-1",
  skill_slug: "skill-1",
  draft_revision: 1,
  title: "Skill 1",
  description: null,
  files: [
    {
      path: "SKILL.md",
      blob_id: "blob-1",
      sha256: "sha-1",
      size_bytes: 12,
      content_type: "text/markdown; charset=utf-8",
    },
  ],
  is_publishable: true,
  validation_errors: [],
}

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockRouterPush }),
}))

jest.mock("@/hooks/use-skills", () => ({
  useSkills: () => ({
    skills: [mockSkill],
    skillsLoading: false,
    skillsError: null,
  }),
  useSkill: () => ({
    skill: mockSkill,
    skillLoading: false,
    skillError: null,
  }),
  useSkillDraft: () => ({
    draft: mockDraft,
    draftLoading: false,
    draftError: null,
  }),
  useSkillVersions: () => ({
    versions: [],
    versionsLoading: false,
    versionsError: null,
  }),
  useSkillDraftFile: () => ({
    draftFile: {
      kind: "inline",
      path: "SKILL.md",
      content_type: "text/markdown; charset=utf-8",
      size_bytes: 12,
      text_content: "# Skill\n",
    },
    draftFileLoading: false,
    draftFileError: null,
  }),
  useCreateSkill: () => ({
    createSkill: mockCreateSkill,
    createSkillPending: false,
    createSkillError: null,
  }),
  useUploadSkill: () => ({
    uploadSkill: mockUploadSkill,
    uploadSkillPending: false,
    uploadSkillError: null,
  }),
  usePatchSkillDraft: () => ({
    patchSkillDraft: mockPatchSkillDraft,
    patchSkillDraftPending: false,
    patchSkillDraftError: null,
  }),
  useCreateSkillDraftUpload: () => ({
    createSkillDraftUpload: mockCreateSkillDraftUpload,
    createSkillDraftUploadPending: false,
    createSkillDraftUploadError: null,
  }),
  usePublishSkill: () => ({
    publishSkill: mockPublishSkill,
    publishSkillPending: false,
    publishSkillError: null,
  }),
  useRestoreSkillVersion: () => ({
    restoreSkillVersion: mockRestoreSkillVersion,
    restoreSkillVersionPending: false,
    restoreSkillVersionError: null,
  }),
}))

const mockUploadFileToSession = uploadFileToSession as jest.MockedFunction<
  typeof uploadFileToSession
>

function createDeferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

describe("skills studio markdown editor selection", () => {
  it("splits and recomposes a frontmatter document", () => {
    const split = splitMarkdownFrontmatter(`---
title: Incident triage
---

Body`)

    expect(split).not.toBeNull()
    expect(split?.frontmatter).toBe("title: Incident triage")
    expect(split?.body.trim()).toBe("Body")
    expect(
      composeMarkdownFrontmatter(split?.frontmatter ?? "", "Updated body")
    ).toBe(`---
title: Incident triage
---

Updated body`)
  })

  it("keeps language selection for non-markdown code files", () => {
    expect(getLanguageForPath("script.py")).toBe("python")
  })
})

describe("useSkillsStudio", () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it("clears staged changes when deleting a new unsaved file", async () => {
    const { result } = renderHook(() =>
      useSkillsStudio({
        workspaceId: "workspace-1",
        initialSkillId: "skill-1",
      })
    )

    await waitFor(() => {
      expect(result.current.selectedPath).toBe("SKILL.md")
    })

    act(() => {
      result.current.onNewFilePathChange("notes.txt")
    })

    act(() => {
      result.current.onCreateNewFile()
    })

    expect(result.current.selectedFile?.path).toBe("notes.txt")
    expect(result.current.hasUnsavedChanges).toBe(true)

    act(() => {
      result.current.onDeleteSelectedFile()
    })

    await waitFor(() => {
      expect(result.current.selectedPath).toBe("SKILL.md")
    })
    expect(result.current.visibleFiles.map((file) => file.path)).toEqual([
      "SKILL.md",
    ])
    expect(result.current.hasUnsavedChanges).toBe(false)
  })

  it("keeps new files staged when their content is cleared back to empty", async () => {
    const { result } = renderHook(() =>
      useSkillsStudio({
        workspaceId: "workspace-1",
        initialSkillId: "skill-1",
      })
    )

    await waitFor(() => {
      expect(result.current.selectedPath).toBe("SKILL.md")
    })

    act(() => {
      result.current.onNewFilePathChange("notes.txt")
    })

    act(() => {
      result.current.onCreateNewFile()
    })

    act(() => {
      result.current.onEditorChange("draft notes")
    })

    act(() => {
      result.current.onEditorChange("")
    })

    expect(result.current.selectedFile?.path).toBe("notes.txt")
    expect(result.current.currentTextValue).toBe("")
    expect(result.current.visibleFiles.map((file) => file.path)).toEqual([
      "notes.txt",
      "SKILL.md",
    ])
    expect(result.current.hasUnsavedChanges).toBe(true)
  })

  it("keeps the full save lifecycle locked while uploads are in flight", async () => {
    const uploadDeferred = createDeferred<void>()
    mockCreateSkillDraftUpload.mockResolvedValue({
      upload_id: "upload-1",
      upload_url: "https://example.com/upload",
      method: "PUT",
      headers: {},
    })
    mockUploadFileToSession.mockImplementation(() => uploadDeferred.promise)
    mockPatchSkillDraft.mockResolvedValue(undefined)

    const { result } = renderHook(() =>
      useSkillsStudio({
        workspaceId: "workspace-1",
        initialSkillId: "skill-1",
      })
    )

    await waitFor(() => {
      expect(result.current.selectedPath).toBe("SKILL.md")
    })

    const file = new File(["updated"], "SKILL.md", {
      type: "text/markdown; charset=utf-8",
    })

    act(() => {
      result.current.onReplaceFile({
        target: { files: [file], value: "" },
      } as unknown as ChangeEvent<HTMLInputElement>)
    })

    expect(result.current.hasUnsavedChanges).toBe(true)

    let savePromise: Promise<void> | undefined
    act(() => {
      savePromise = result.current.onSaveWorkingCopy()
    })

    await waitFor(() => {
      expect(result.current.saveWorkingCopyPending).toBe(true)
    })
    expect(mockPatchSkillDraft).not.toHaveBeenCalled()

    uploadDeferred.resolve()

    await act(async () => {
      await savePromise
    })

    await waitFor(() => {
      expect(result.current.saveWorkingCopyPending).toBe(false)
    })
    expect(mockPatchSkillDraft).toHaveBeenCalledTimes(1)
  })

  it("skips save requests when no draft changes are staged", async () => {
    const { result } = renderHook(() =>
      useSkillsStudio({
        workspaceId: "workspace-1",
        initialSkillId: "skill-1",
      })
    )

    await waitFor(() => {
      expect(result.current.selectedPath).toBe("SKILL.md")
    })

    await act(async () => {
      await result.current.onSaveWorkingCopy()
    })

    expect(result.current.saveWorkingCopyPending).toBe(false)
    expect(mockCreateSkillDraftUpload).not.toHaveBeenCalled()
    expect(mockPatchSkillDraft).not.toHaveBeenCalled()
  })

  it("preserves edits made while a save is in flight", async () => {
    const patchDeferred = createDeferred<void>()
    mockPatchSkillDraft.mockImplementation(() => patchDeferred.promise)

    const { result } = renderHook(() =>
      useSkillsStudio({
        workspaceId: "workspace-1",
        initialSkillId: "skill-1",
      })
    )

    await waitFor(() => {
      expect(result.current.selectedPath).toBe("SKILL.md")
    })

    act(() => {
      result.current.markdownEditorActivatedRef.current = true
      result.current.onEditorChange("First draft")
    })

    let savePromise: Promise<void> | undefined
    act(() => {
      savePromise = result.current.onSaveWorkingCopy()
    })

    await waitFor(() => {
      expect(result.current.saveWorkingCopyPending).toBe(true)
    })
    expect(mockPatchSkillDraft).toHaveBeenCalledWith({
      skillId: "skill-1",
      requestBody: {
        base_revision: 1,
        operations: [
          {
            op: "upsert_text_file",
            path: "SKILL.md",
            content: "First draft",
            content_type: "text/markdown; charset=utf-8",
          },
        ],
      },
    })

    act(() => {
      result.current.onEditorChange("Second draft")
    })

    patchDeferred.resolve()

    await act(async () => {
      await savePromise
    })

    expect(result.current.saveWorkingCopyPending).toBe(false)
    expect(result.current.hasUnsavedChanges).toBe(true)
    expect(result.current.currentTextValue).toBe("Second draft")
  })
})
