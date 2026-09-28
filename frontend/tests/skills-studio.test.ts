import { act, renderHook, waitFor } from "@testing-library/react"
import type { SkillDraftRead, SkillRead } from "@/client"
import { useSkillsStudio } from "@/components/skills/use-skills-studio"
import {
  composeMarkdownFrontmatter,
  splitMarkdownFrontmatter,
} from "@/lib/markdown-frontmatter"
import {
  fileToUploadEntry,
  getLanguageForPath,
  validateSkillDraftPath,
  validateSkillName,
} from "@/lib/skills-studio"

const mockRouterPush = jest.fn()
const mockCreateSkill = jest.fn()
const mockUploadSkill = jest.fn()
const mockPatchSkillDraft = jest.fn()
const mockCreateSkillDraftUpload = jest.fn()
const mockPublishSkill = jest.fn()
const mockRestoreSkillVersion = jest.fn()
const mockDeleteSkill = jest.fn()
let mockDraftLoading = false

jest.mock("@/lib/skills-studio", () => {
  const actual = jest.requireActual("@/lib/skills-studio")
  return {
    ...actual,
    computeFileSha256: jest.fn(async () => "sha-upload"),
    fileToUploadEntry: jest.fn(),
  }
})

const mockSkillOne: SkillRead = {
  id: "skill-1",
  workspace_id: "workspace-1",
  name: "skill-1",
  slug: "skill-1",
  description: null,
  current_version_id: null,
  draft_revision: 1,
  created_at: "2026-04-10T00:00:00.000Z",
  updated_at: "2026-04-10T00:00:00.000Z",
  deleted_at: null,
  current_version: null,
  is_draft_publishable: true,
  draft_validation_errors: [],
  draft_file_count: 1,
}

const mockSkillTwo: SkillRead = {
  id: "skill-2",
  workspace_id: "workspace-1",
  name: "skill-2",
  slug: "skill-2",
  description: null,
  current_version_id: null,
  draft_revision: 3,
  created_at: "2026-04-10T00:00:00.000Z",
  updated_at: "2026-04-10T00:00:00.000Z",
  deleted_at: null,
  current_version: null,
  is_draft_publishable: true,
  draft_validation_errors: [],
  draft_file_count: 1,
}

const mockSkills = [mockSkillOne, mockSkillTwo]

const mockSkillsById: Record<string, SkillRead> = {
  [mockSkillOne.id]: mockSkillOne,
  [mockSkillTwo.id]: mockSkillTwo,
}

const mockDraftOne: SkillDraftRead = {
  skill_id: "skill-1",
  skill_name: "skill-1",
  draft_revision: 1,
  name: "Skill 1",
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

const mockDraftTwo: SkillDraftRead = {
  skill_id: "skill-2",
  skill_name: "skill-2",
  draft_revision: 3,
  name: "Skill 2",
  description: null,
  files: [
    {
      path: "README.md",
      blob_id: "blob-2",
      sha256: "sha-2",
      size_bytes: 16,
      content_type: "text/markdown; charset=utf-8",
    },
  ],
  is_publishable: true,
  validation_errors: [],
}

const mockDraftsBySkillId: Record<string, SkillDraftRead> = {
  [mockDraftOne.skill_id]: mockDraftOne,
  [mockDraftTwo.skill_id]: mockDraftTwo,
}

const mockDraftFileContentsBySkillId: Record<string, Record<string, string>> = {
  "skill-1": {
    "SKILL.md": "# Skill\n",
  },
  "skill-2": {
    "README.md": "# Skill 2\n",
  },
}

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockRouterPush }),
  useSearchParams: () => {
    const { startTransition, useEffect, useState } =
      jest.requireActual<typeof import("react")>("react")
    const [search, setSearch] = useState(window.location.search)
    useEffect(() => {
      const notify = () =>
        startTransition(() => setSearch(window.location.search))
      window.addEventListener("popstate", notify)
      return () => window.removeEventListener("popstate", notify)
    }, [])
    return new URLSearchParams(search)
  },
}))

jest.mock("@/hooks/use-skills", () => ({
  useSkills: () => ({
    skills: mockSkills,
    skillsLoading: false,
    skillsError: null,
  }),
  useSkill: (_workspaceId: string, skillId: string | null) => ({
    skill: skillId ? mockSkillsById[skillId] : undefined,
    skillLoading: false,
    skillError: null,
  }),
  useSkillDraft: (_workspaceId: string, skillId: string | null) => ({
    draft:
      skillId && !mockDraftLoading ? mockDraftsBySkillId[skillId] : undefined,
    draftLoading: mockDraftLoading,
    draftError: null,
  }),
  useSkillVersions: () => ({
    versions: [],
    versionsLoading: false,
    versionsError: null,
  }),
  useSkillDraftFile: (
    _workspaceId: string,
    skillId: string | null,
    path: string | null
  ) => {
    const textContent =
      skillId && path
        ? mockDraftFileContentsBySkillId[skillId]?.[path]
        : undefined
    return {
      draftFile:
        textContent === undefined || path === null
          ? undefined
          : {
              kind: "inline",
              path,
              content_type: "text/markdown; charset=utf-8",
              size_bytes: textContent.length,
              text_content: textContent,
            },
      draftFileLoading: false,
      draftFileError: null,
    }
  },
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
  useDeleteSkill: () => ({
    deleteSkill: mockDeleteSkill,
    deleteSkillPending: false,
    deleteSkillError: null,
  }),
}))

const mockFileToUploadEntry = fileToUploadEntry as jest.MockedFunction<
  typeof fileToUploadEntry
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

  it("does not include the frontmatter separator blank line in the editable body", () => {
    const split = splitMarkdownFrontmatter(`---
name: incident-triage
description: Handles incidents.
---

Use this skill for triage.`)

    expect(split?.body).toBe("Use this skill for triage.")
  })

  it("preserves intentional extra leading blank lines after frontmatter", () => {
    const markdown = `---
name: incident-triage
description: Handles incidents.
---


Use this skill for triage.`
    const split = splitMarkdownFrontmatter(markdown)

    expect(split?.body).toBe("\nUse this skill for triage.")
    expect(
      composeMarkdownFrontmatter(split?.frontmatter ?? "", split?.body ?? "")
    ).toBe(markdown)
  })

  it("keeps language selection for non-markdown code files", () => {
    expect(getLanguageForPath("script.py")).toBe("python")
  })

  it("validates draft file paths against backend normalization rules", () => {
    expect(validateSkillDraftPath("docs/readme.md")).toBeNull()
    expect(validateSkillDraftPath("")).toBe("File path is required.")
    expect(validateSkillDraftPath("../notes.md")).toBe(
      "File path cannot escape the skill root."
    )
    expect(validateSkillDraftPath("foo//bar.md")).toBe(
      "Use a normalized path without duplicate, leading, or trailing separators."
    )
    expect(validateSkillDraftPath("./SKILL.md")).toBe(
      "Use a normalized path without duplicate, leading, or trailing separators."
    )
    expect(validateSkillDraftPath("docs\\notes.md")).toBe(
      "Use forward slashes instead of backslashes."
    )
  })

  it("validates skill names with the Zod-backed schema", () => {
    expect(validateSkillName("threat-intel")).toBeNull()
    expect(validateSkillName("threat--intel")).toBe(
      "Use lowercase letters, numbers, and single hyphens (e.g. threat-intel)."
    )
    expect(validateSkillName("threat-")).toBe("Name cannot end with a hyphen.")
    expect(validateSkillName("")).toBe("Name is required.")
  })
})

describe("useSkillsStudio", () => {
  beforeEach(() => {
    jest.restoreAllMocks()
    jest.clearAllMocks()
    mockDraftLoading = false
    mockDraftOne.files?.splice(1)
    window.history.replaceState(null, "", "/skills/skill-1")
    const replaceState = window.history.replaceState.bind(window.history)
    jest.spyOn(window.history, "replaceState").mockImplementation((...args) => {
      replaceState(...args)
      // Next's history integration notifies useSearchParams on URL changes.
      window.dispatchEvent(new PopStateEvent("popstate"))
    })
    mockFileToUploadEntry.mockImplementation(async (file, relativePath) => ({
      path: relativePath,
      content_base64: "encoded-upload",
      content_type: file.type || undefined,
    }))
  })

  it("defaults to SKILL.md and preserves an encoded file selection on remount", () => {
    const path = "references/tool notes.md"
    const original = mockDraftOne.files?.[0]
    if (!original) throw new Error("Expected the SKILL.md fixture")
    mockDraftOne.files?.push({ ...original, path })
    window.history.replaceState(null, "", "/skills/skill-1?view=editor#details")
    const first = renderHook(() =>
      useSkillsStudio({ workspaceId: "workspace-1", skillId: "skill-1" })
    )
    expect(first.result.current.selectedPath).toBe("SKILL.md")
    act(() => first.result.current.onSelectPath(path))
    expect(first.result.current.selectedPath).toBe(path)
    expect(window.location.search).toContain("file=references%2Ftool+notes.md")
    expect(new URLSearchParams(window.location.search).get("view")).toBe(
      "editor"
    )
    expect(window.location.hash).toBe("#details")
    first.unmount()
    const refreshed = renderHook(() =>
      useSkillsStudio({ workspaceId: "workspace-1", skillId: "skill-1" })
    )
    expect(refreshed.result.current.selectedPath).toBe(path)
  })

  it("keeps a deep link while loading and falls back when its file is missing", () => {
    mockDraftLoading = true
    window.history.replaceState(null, "", "/skills/skill-1?file=missing.md")
    const { result, rerender } = renderHook(() =>
      useSkillsStudio({ workspaceId: "workspace-1", skillId: "skill-1" })
    )
    expect(new URLSearchParams(window.location.search).get("file")).toBe(
      "missing.md"
    )
    mockDraftLoading = false
    rerender()
    expect(result.current.selectedPath).toBe("SKILL.md")
    expect(new URLSearchParams(window.location.search).get("file")).toBe(
      "SKILL.md"
    )
  })

  it("keeps new files staged when their content is cleared back to empty", async () => {
    const { result } = renderHook(() =>
      useSkillsStudio({
        workspaceId: "workspace-1",
        skillId: "skill-1",
      })
    )

    await waitFor(() => {
      expect(result.current.selectedPath).toBe("SKILL.md")
    })

    act(() => {
      result.current.onBeginCreate()
    })

    act(() => {
      result.current.onSubmitCreate("notes.txt")
    })
    expect(result.current.selectedPath).toBe("notes.txt")

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

  it("rejects invalid new file paths before staging draft changes", async () => {
    const { result } = renderHook(() =>
      useSkillsStudio({
        workspaceId: "workspace-1",
        skillId: "skill-1",
      })
    )

    await waitFor(() => {
      expect(result.current.selectedPath).toBe("SKILL.md")
    })

    act(() => {
      result.current.onBeginCreate()
    })

    act(() => {
      result.current.onSubmitCreate("../notes.md")
    })

    expect(result.current.pendingCreate).toBe(true)
    expect(result.current.pendingCreateError).not.toBeNull()
    expect(result.current.visibleFiles.map((file) => file.path)).toEqual([
      "SKILL.md",
    ])
    expect(result.current.hasUnsavedChanges).toBe(false)
  })

  it("skips save requests when no draft changes are staged", async () => {
    const { result } = renderHook(() =>
      useSkillsStudio({
        workspaceId: "workspace-1",
        skillId: "skill-1",
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
        skillId: "skill-1",
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
