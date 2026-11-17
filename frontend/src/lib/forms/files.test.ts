import { formsUploadFormFile } from "@/client"
import { getFileAnswers, uploadFormFile } from "@/lib/forms/files"

jest.mock("@/client", () => ({ formsUploadFormFile: jest.fn() }))

const upload = jest.mocked(formsUploadFormFile)

// Downloads are covered through the Activity tab in `form-detail.test.tsx`.

describe("uploadFormFile", () => {
  it("posts the file for its field and rejects with the server's message", async () => {
    const file = new File(["x"], "quote.pdf", { type: "application/pdf" })
    const args = { workspaceId: "ws-1", formId: "form-1", field: "quote" }
    upload.mockResolvedValueOnce({
      id: "file-1",
      name: "quote.pdf",
      content_type: "application/pdf",
      size: 1,
      sha256: "0".repeat(64),
    })

    await expect(uploadFormFile({ ...args, file })).resolves.toEqual({
      id: "file-1",
    })
    expect(upload).toHaveBeenCalledWith({ ...args, formData: { file } })

    upload.mockRejectedValueOnce(
      Object.assign(new Error("Unsupported Media Type"), {
        status: 415,
        body: {
          detail: {
            code: "file_type_not_allowed",
            message: "This field does not take this type of file",
          },
        },
      })
    )
    await expect(uploadFormFile({ ...args, file })).rejects.toThrow(
      "This field does not take this type of file"
    )
    upload.mockRejectedValueOnce(new Error("Network Error"))
    await expect(uploadFormFile({ ...args, file })).rejects.toThrow(
      "Upload failed"
    )
  })
})

describe("getFileAnswers", () => {
  const file = {
    id: "file-1",
    name: "quote.pdf",
    content_type: "application/pdf",
    size: 2048,
    sha256: "0".repeat(64),
  }

  it.each([
    ["one file", file, [file]],
    ["a list of files", [file, file], [file, file]],
    ["an empty list", [], null],
    ["a user", { user_id: "user-1", email: "maya.okafor@example.com" }, null],
    ["a string", "quote.pdf", null],
    ["a mixed list", [file, "x"], null],
    ["nothing", null, null],
  ])("reads %s", (_name, value, expected) => {
    expect(getFileAnswers(value)).toEqual(expected)
  })
})
