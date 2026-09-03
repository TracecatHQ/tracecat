/**
 * @jest-environment jsdom
 */

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { GitHubAppManualForm } from "@/components/organization/org-vcs-github-manual-form"

const mockMutateAsync = jest.fn()
const mockToast = jest.fn()

jest.mock("@/components/ui/use-toast", () => ({
  useToast: () => ({ toast: mockToast }),
}))

jest.mock("@/lib/hooks", () => ({
  useGitHubAppCredentials: () => ({
    saveCredentials: {
      mutateAsync: mockMutateAsync,
      isPending: false,
      isError: false,
      error: null,
    },
  }),
}))

function createDataTransfer(file: File) {
  return {
    files: [file],
    clearData: jest.fn(),
  }
}

type FileReaderEventHandler =
  | ((this: FileReader, event: ProgressEvent<FileReader>) => unknown)
  | null

function installDeferredFileReader() {
  const originalFileReader = window.FileReader
  const deferredReader = {
    result: null as string | ArrayBuffer | null,
    onload: null as FileReaderEventHandler,
    onerror: null as FileReaderEventHandler,
    readAsText: jest.fn(),
  }

  Object.defineProperty(window, "FileReader", {
    configurable: true,
    writable: true,
    value: jest.fn(() => deferredReader as unknown as FileReader),
  })

  return {
    deferredReader,
    restoreFileReader: () => {
      Object.defineProperty(window, "FileReader", {
        configurable: true,
        writable: true,
        value: originalFileReader,
      })
    },
  }
}

describe("GitHubAppManualForm", () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it("opens the file picker when the dropzone is clicked", () => {
    const clickSpy = jest
      .spyOn(HTMLInputElement.prototype, "click")
      .mockImplementation(() => {})

    render(<GitHubAppManualForm />)

    fireEvent.click(screen.getByTestId("github-app-private-key-dropzone"))

    expect(clickSpy).toHaveBeenCalledTimes(1)
    clickSpy.mockRestore()
  })

  it("loads a dropped PEM file into the private key field", async () => {
    const pem = [
      "-----BEGIN PRIVATE KEY-----",
      "MIIEpAIBAAKCAQEA",
      "-----END PRIVATE KEY-----",
    ].join("\n")
    const file = new File([pem], "github-app.private-key.pem", {
      type: "application/x-pem-file",
    })

    render(<GitHubAppManualForm />)

    fireEvent.drop(screen.getByTestId("github-app-private-key-dropzone"), {
      dataTransfer: createDataTransfer(file),
    })

    await waitFor(() => {
      expect(screen.getByLabelText("Private Key *")).toHaveValue(pem)
    })
    expect(screen.getByText("github-app.private-key.pem")).toBeInTheDocument()
  })

  it("clears existing private key when rejecting non-PEM files", () => {
    const existingPem = [
      "-----BEGIN PRIVATE KEY-----",
      "MIIEpAIBAAKCAQEA",
      "-----END PRIVATE KEY-----",
    ].join("\n")
    const file = new File(["not a pem"], "private-key.txt", {
      type: "text/plain",
    })

    render(<GitHubAppManualForm />)

    const privateKeyInput = screen.getByLabelText("Private Key *")
    fireEvent.change(privateKeyInput, {
      target: { value: existingPem },
    })
    expect(privateKeyInput).toHaveValue(existingPem)

    fireEvent.drop(screen.getByTestId("github-app-private-key-dropzone"), {
      dataTransfer: createDataTransfer(file),
    })

    expect(privateKeyInput).toHaveValue("")
    expect(screen.getByText("Upload a .pem file.")).toBeInTheDocument()
  })

  it("ignores stale PEM reads after a rejected replacement", () => {
    const stalePem = [
      "-----BEGIN PRIVATE KEY-----",
      "MIIEpAIBAAKCAQEA",
      "-----END PRIVATE KEY-----",
    ].join("\n")
    const pemFile = new File([stalePem], "github-app.private-key.pem", {
      type: "application/x-pem-file",
    })
    const invalidFile = new File(["not a pem"], "private-key.txt", {
      type: "text/plain",
    })
    const { deferredReader, restoreFileReader } = installDeferredFileReader()

    try {
      render(<GitHubAppManualForm />)

      const dropzone = screen.getByTestId("github-app-private-key-dropzone")
      const privateKeyInput = screen.getByLabelText("Private Key *")

      fireEvent.drop(dropzone, {
        dataTransfer: createDataTransfer(pemFile),
      })
      expect(deferredReader.readAsText).toHaveBeenCalledWith(pemFile)

      fireEvent.drop(dropzone, {
        dataTransfer: createDataTransfer(invalidFile),
      })
      expect(privateKeyInput).toHaveValue("")
      expect(screen.getByText("Upload a .pem file.")).toBeInTheDocument()

      deferredReader.result = stalePem
      act(() => {
        deferredReader.onload?.call(
          deferredReader as unknown as FileReader,
          new ProgressEvent("load") as ProgressEvent<FileReader>
        )
      })

      expect(privateKeyInput).toHaveValue("")
      expect(
        screen.queryByText("github-app.private-key.pem")
      ).not.toBeInTheDocument()
    } finally {
      restoreFileReader()
    }
  })

  it("prevents saving an old private key while a PEM is loading", async () => {
    const existingPem = [
      "-----BEGIN PRIVATE KEY-----",
      "MIIEpAIBAAKCAQEA",
      "-----END PRIVATE KEY-----",
    ].join("\n")
    const replacementPem = [
      "-----BEGIN PRIVATE KEY-----",
      "MIIEvQIBADANBgkqhkiG9w0BAQEFAASC",
      "-----END PRIVATE KEY-----",
    ].join("\n")
    const file = new File([replacementPem], "replacement.private-key.pem", {
      type: "application/x-pem-file",
    })
    const { deferredReader, restoreFileReader } = installDeferredFileReader()

    try {
      render(<GitHubAppManualForm />)

      fireEvent.change(screen.getByLabelText("GitHub App ID *"), {
        target: { value: "123456" },
      })
      const privateKeyInput = screen.getByLabelText("Private Key *")
      fireEvent.change(privateKeyInput, {
        target: { value: existingPem },
      })
      expect(privateKeyInput).toHaveValue(existingPem)

      fireEvent.drop(screen.getByTestId("github-app-private-key-dropzone"), {
        dataTransfer: createDataTransfer(file),
      })

      expect(deferredReader.readAsText).toHaveBeenCalledWith(file)
      expect(privateKeyInput).toHaveValue("")

      fireEvent.click(screen.getByRole("button", { name: "Save credentials" }))

      await waitFor(() => {
        expect(screen.getByText("Private key is required")).toBeInTheDocument()
      })
      expect(mockMutateAsync).not.toHaveBeenCalled()
    } finally {
      restoreFileReader()
    }
  })
})
