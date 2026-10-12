import { formsDownloadFormFile, formsUploadFormFile } from "@/client"
import {
  checkFormFile,
  downloadFormFile,
  getFileAnswers,
  uploadFormFile,
} from "@/lib/forms/files"

jest.mock("@/client", () => ({
  formsUploadFormFile: jest.fn(),
  formsDownloadFormFile: jest.fn(),
}))

const upload = jest.mocked(formsUploadFormFile)
const download = jest.mocked(formsDownloadFormFile)

const MB = 1024 * 1024

function picked(name: string, type: string, size = 10) {
  return { name, type, size }
}

describe("checkFormFile", () => {
  const config = {
    accept: [".pdf", "image/*"],
    max_files: 2,
    max_file_size: MB,
  }

  it.each([
    ["a matching extension", picked("quote.PDF", "")],
    ["a matching wildcard type", picked("photo.png", "image/png")],
    ["a file at the size limit", picked("quote.pdf", "application/pdf", MB)],
  ])("takes %s", (_name, file) => {
    expect(checkFormFile(file, config)).toBeNull()
  })

  it("rejects a type the field does not accept", () => {
    expect(checkFormFile(picked("notes.txt", "text/plain"), config)).toBe(
      "This field takes .pdf, image/*"
    )
  })

  it("rejects a file over the size limit and an empty file", () => {
    expect(
      checkFormFile(picked("quote.pdf", "application/pdf", MB + 1), config)
    ).toBe("Files can be at most 1 MB")
    expect(
      checkFormFile(picked("quote.pdf", "application/pdf", 0), config)
    ).toBe("The file is empty")
  })

  it("matches an exact MIME type and takes anything when accept is unset", () => {
    expect(
      checkFormFile(picked("a.csv", "text/csv"), { accept: ["text/csv"] })
    ).toBeNull()
    expect(
      checkFormFile(picked("a.txt", "text/plain"), { accept: ["text/csv"] })
    ).not.toBeNull()
    expect(checkFormFile(picked("a.txt", "text/plain"), null)).toBeNull()
  })
})

describe("uploadFormFile", () => {
  const file = new File(["x"], "quote.pdf", { type: "application/pdf" })
  const args = { workspaceId: "ws-1", formId: "form-1", field: "quote", file }

  it("posts the file for its field and resolves to the file ID", async () => {
    upload.mockResolvedValueOnce({
      id: "file-1",
      name: "quote.pdf",
      content_type: "application/pdf",
      size: 1,
      sha256: "0".repeat(64),
    })

    await expect(uploadFormFile(args)).resolves.toEqual({ id: "file-1" })
    expect(upload).toHaveBeenCalledWith({
      workspaceId: "ws-1",
      formId: "form-1",
      field: "quote",
      formData: { file },
    })
  })

  it("rejects with the server's message, or a generic one", async () => {
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
    await expect(uploadFormFile(args)).rejects.toThrow(
      "This field does not take this type of file"
    )

    upload.mockRejectedValueOnce(new Error("Network Error"))
    await expect(uploadFormFile(args)).rejects.toThrow("Upload failed")
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

  it("reads one file or a list of files", () => {
    expect(getFileAnswers(file)).toEqual([file])
    expect(getFileAnswers([file, file])).toEqual([file, file])
  })

  it.each([
    ["an empty list", []],
    ["a user", { user_id: "user-1", email: "maya.okafor@example.com" }],
    ["a string", "quote.pdf"],
    ["a mixed list", [file, "x"]],
    ["nothing", null],
  ])("returns null for %s", (_name, value) => {
    expect(getFileAnswers(value)).toBeNull()
  })
})

describe("downloadFormFile", () => {
  it("follows the short-lived URL without leaving the page", async () => {
    download.mockResolvedValueOnce({
      download_url: "https://storage.example.com/forms/a/b/c?signature=x",
      file_name: "quote.pdf",
      content_type: "application/pdf",
    })
    const clicked: string[] = []
    const click = jest
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(function (this: HTMLAnchorElement) {
        clicked.push(this.href)
      })

    await downloadFormFile({
      workspaceId: "ws-1",
      formId: "form-1",
      fileId: "file-1",
    })

    expect(download).toHaveBeenCalledWith({
      workspaceId: "ws-1",
      formId: "form-1",
      fileId: "file-1",
    })
    expect(clicked).toEqual([
      "https://storage.example.com/forms/a/b/c?signature=x",
    ])
    expect(document.querySelector("a")).toBeNull()
    click.mockRestore()
  })
})
