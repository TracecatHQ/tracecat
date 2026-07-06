import { buildAttachmentUploadPolicy } from "@/components/cases/case-attachments-section"
import { describeAttachmentUploadError } from "@/lib/cases/attachment-errors"

describe("case attachment upload policy", () => {
  it("disables uploads when effective extensions are explicitly empty", () => {
    expect(buildAttachmentUploadPolicy([])).toEqual({
      uploadsDisabled: true,
    })
  })

  it("builds an accept attribute for non-empty effective extensions", () => {
    expect(buildAttachmentUploadPolicy([".pdf", ".png"])).toEqual({
      uploadsDisabled: false,
      acceptAttribute: ".pdf,.png",
    })
  })

  it("inherits server defaults for null or undefined effective extensions", () => {
    expect(buildAttachmentUploadPolicy(null)).toEqual({
      uploadsDisabled: false,
    })
    expect(buildAttachmentUploadPolicy(undefined)).toEqual({
      uploadsDisabled: false,
    })
  })
})

describe("describeAttachmentUploadError", () => {
  it("does not fall back to default file types for empty allowed extensions", () => {
    const toast = describeAttachmentUploadError(
      {
        status: 415,
        message: "Unsupported file extension",
        body: {
          detail: {
            error: "unsupported_file_extension",
            allowed_extensions: [],
          },
        },
      },
      "blocked.exe"
    )

    expect(toast).toEqual({
      title: "Attachment uploads disabled",
      description:
        "blocked.exe cannot be uploaded because uploads are disabled for this workspace.",
    })
  })

  it("preserves default file type fallback when allowed extensions are absent", () => {
    const toast = describeAttachmentUploadError(
      {
        status: 415,
        message: "Unsupported file extension",
        body: {
          detail: {
            error: "unsupported_file_extension",
          },
        },
      },
      "blocked.exe"
    )

    expect(toast).toEqual({
      title: "File type not supported",
      description:
        "blocked.exe cannot be uploaded. Allowed file types: txt, pdf, png, jpeg, gif, csv",
    })
  })
})
