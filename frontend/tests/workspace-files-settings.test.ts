import { buildFilesSettingsUpdate } from "@/components/settings/workspace-files-settings"

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
})
