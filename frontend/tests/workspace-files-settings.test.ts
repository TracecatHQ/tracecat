import {
  buildFilesSettingsUpdate,
  filesSettingsSchema,
} from "@/components/settings/workspace-files-settings"

describe("workspace files settings", () => {
  it("sends null when attachment override lists are cleared", () => {
    const result = buildFilesSettingsUpdate({
      allowed_attachment_extensions: [],
      allowed_attachment_mime_types: [],
      validate_attachment_magic_number: true,
    })

    expect(result).toEqual({
      allowed_attachment_extensions: null,
      allowed_attachment_mime_types: null,
      validate_attachment_magic_number: true,
    })
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
