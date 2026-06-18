/**
 * @jest-environment jsdom
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react"
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
})
