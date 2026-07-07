import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { createElement } from "react"
import type { WorkspaceRead } from "@/client"
import {
  buildAttachmentTags,
  buildFilesSettingsUpdate,
  describeAttachmentAllowlistState,
  filesSettingsSchema,
  WorkspaceFilesSettings,
} from "@/components/settings/workspace-files-settings"
import { useWorkspaceSettings } from "@/lib/hooks"

jest.mock("@/components/tags-input", () => {
  const React = require("react") as typeof import("react")

  interface MockTag {
    id: string
    text: string
  }

  interface MockCustomTagInputProps {
    placeholder?: string
    tags?: MockTag[]
    setTags: (tags: MockTag[]) => void
  }

  const CustomTagInput = React.forwardRef<
    HTMLDivElement,
    MockCustomTagInputProps
  >(function CustomTagInput({ placeholder, tags = [], setTags }, ref) {
    return React.createElement(
      "div",
      { "data-testid": placeholder, ref },
      tags.map((tag) => React.createElement("span", { key: tag.id }, tag.text)),
      React.createElement(
        "button",
        {
          "aria-label": `Clear ${placeholder}`,
          onClick: () => setTags([]),
          type: "button",
        },
        "Clear"
      )
    )
  })

  return { CustomTagInput }
})

jest.mock("@/lib/hooks", () => ({
  useWorkspaceSettings: jest.fn(),
}))

const mockUseWorkspaceSettings = jest.mocked(useWorkspaceSettings)

function createWorkspace(): WorkspaceRead {
  return {
    id: "workspace-id",
    name: "Workspace",
    organization_id: "organization-id",
    settings: {
      allowed_attachment_extensions: [".exe"],
      allowed_attachment_mime_types: ["application/x-msdownload"],
      effective_allowed_attachment_extensions: [".exe"],
      effective_allowed_attachment_mime_types: ["application/x-msdownload"],
      validate_attachment_magic_number: true,
    },
  }
}

describe("workspace files settings", () => {
  beforeEach(() => {
    mockUseWorkspaceSettings.mockReturnValue({
      deleteWorkspace: jest.fn(),
      isDeleting: false,
      isUpdating: false,
      updateWorkspace: jest.fn().mockResolvedValue(undefined),
    } as unknown as ReturnType<typeof useWorkspaceSettings>)
  })

  it("clears stale custom extension tags when restoring inherited defaults", async () => {
    const workspace = createWorkspace()
    const updateWorkspace = jest.fn().mockResolvedValue(undefined)
    mockUseWorkspaceSettings.mockReturnValue({
      deleteWorkspace: jest.fn(),
      isDeleting: false,
      isUpdating: false,
      updateWorkspace,
    } as unknown as ReturnType<typeof useWorkspaceSettings>)

    render(createElement(WorkspaceFilesSettings, { workspace }))

    expect(screen.getByText(".exe")).toBeInTheDocument()

    fireEvent.click(
      screen.getByRole("button", {
        name: "Restore inherited file extensions",
      })
    )

    expect(screen.queryByText(".exe")).not.toBeInTheDocument()
    expect(
      screen.getByText(/Current policy: Inherited defaults/)
    ).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "Save" }))

    await waitFor(() => {
      expect(updateWorkspace).toHaveBeenCalledWith({
        settings: {
          allowed_attachment_extensions: null,
          allowed_attachment_mime_types: ["application/x-msdownload"],
          validate_attachment_magic_number: true,
        },
      })
    })
  })

  it("clears stale custom MIME type tags when restoring inherited defaults", async () => {
    const workspace = createWorkspace()
    const updateWorkspace = jest.fn().mockResolvedValue(undefined)
    mockUseWorkspaceSettings.mockReturnValue({
      deleteWorkspace: jest.fn(),
      isDeleting: false,
      isUpdating: false,
      updateWorkspace,
    } as unknown as ReturnType<typeof useWorkspaceSettings>)

    render(createElement(WorkspaceFilesSettings, { workspace }))

    expect(screen.getByText("application/x-msdownload")).toBeInTheDocument()

    fireEvent.click(
      screen.getByRole("button", {
        name: "Restore inherited MIME types",
      })
    )

    expect(
      screen.queryByText("application/x-msdownload")
    ).not.toBeInTheDocument()
    expect(
      screen.getByText(/Current policy: Inherited defaults/)
    ).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "Save" }))

    await waitFor(() => {
      expect(updateWorkspace).toHaveBeenCalledWith({
        settings: {
          allowed_attachment_extensions: [".exe"],
          allowed_attachment_mime_types: null,
          validate_attachment_magic_number: true,
        },
      })
    })
  })

  it("sends empty arrays when attachment override lists are cleared", () => {
    const result = buildFilesSettingsUpdate({
      allowed_attachment_extensions: [],
      allowed_attachment_mime_types: [],
      validate_attachment_magic_number: true,
    })

    expect(result).toEqual({
      allowed_attachment_extensions: [],
      allowed_attachment_mime_types: [],
      validate_attachment_magic_number: true,
    })
  })

  it("round-trips stored empty attachment allowlists as empty tag lists", () => {
    expect(buildAttachmentTags([], "ext")).toEqual([])
    expect(buildAttachmentTags([], "mime")).toEqual([])
    expect(buildAttachmentTags(null, "ext")).toBeUndefined()
    expect(buildAttachmentTags(undefined, "mime")).toBeUndefined()
  })

  it("keeps configured attachment override lists when present", () => {
    const result = buildFilesSettingsUpdate({
      allowed_attachment_extensions: [{ id: "ext-1", text: ".pdf" }],
      allowed_attachment_mime_types: [
        { id: "mime-1", text: "application/pdf" },
      ],
      validate_attachment_magic_number: false,
    })

    expect(result).toEqual({
      allowed_attachment_extensions: [".pdf"],
      allowed_attachment_mime_types: ["application/pdf"],
      validate_attachment_magic_number: false,
    })
  })

  it("sends null when reset to inherited defaults", () => {
    const result = buildFilesSettingsUpdate(
      {
        allowed_attachment_extensions: [{ id: "ext-1", text: ".pdf" }],
        allowed_attachment_mime_types: [
          { id: "mime-1", text: "application/pdf" },
        ],
        validate_attachment_magic_number: true,
      },
      {
        inheritAttachmentExtensions: true,
        inheritAttachmentMimeTypes: true,
      }
    )

    expect(result).toEqual({
      allowed_attachment_extensions: null,
      allowed_attachment_mime_types: null,
      validate_attachment_magic_number: true,
    })
  })

  it("describes inherited, disabled, and custom attachment allowlist states", () => {
    expect(describeAttachmentAllowlistState(undefined, false)).toEqual({
      label: "Inherited defaults",
      description: "Uses system attachment defaults.",
    })
    expect(describeAttachmentAllowlistState([], false)).toEqual({
      label: "Uploads disabled",
      description:
        "No values are allowed until you add entries or restore inherited defaults.",
    })
    expect(
      describeAttachmentAllowlistState([{ id: "ext-1", text: ".pdf" }], false)
    ).toEqual({
      label: "Custom allowlist",
      description: "Only the listed values are allowed.",
    })
    expect(describeAttachmentAllowlistState([], true)).toEqual({
      label: "Inherited defaults",
      description: "Uses system attachment defaults.",
    })
  })

  it("trims attachment override tags during validation", () => {
    const result = filesSettingsSchema.safeParse({
      allowed_attachment_extensions: [{ id: "ext-1", text: "  .pdf  " }],
      allowed_attachment_mime_types: [
        { id: "mime-1", text: "  application/pdf  " },
      ],
      validate_attachment_magic_number: true,
    })

    expect(result.success).toBe(true)
    if (!result.success) {
      throw new Error("Expected trimmed attachment tags to pass validation")
    }
    expect(result.data.allowed_attachment_extensions).toEqual([
      { id: "ext-1", text: ".pdf" },
    ])
    expect(result.data.allowed_attachment_mime_types).toEqual([
      { id: "mime-1", text: "application/pdf" },
    ])
  })

  it("rejects attachment override tags that are empty after trimming", () => {
    const result = filesSettingsSchema.safeParse({
      allowed_attachment_extensions: [{ id: "ext-1", text: "   " }],
      validate_attachment_magic_number: true,
    })

    expect(result.success).toBe(false)
  })
})
