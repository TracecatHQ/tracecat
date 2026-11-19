import { fireEvent, render, screen } from "@testing-library/react"
import {
  SecretReferenceCheckButton,
  SecretReferenceCheckHint,
  SecretReferenceCheckScope,
} from "@/components/workspaces/secret-reference-check-button"

const mockCheckReference = jest.fn()

jest.mock("@/hooks/use-secret-stores", () => ({
  describeApiError: () => "API error",
  useSecretReferences: () => ({
    checkReference: mockCheckReference,
  }),
}))

function renderCheck(
  source:
    | "aws_secrets_manager"
    | "bitwarden_secrets_manager" = "aws_secrets_manager"
) {
  render(
    <SecretReferenceCheckScope
      workspaceId="workspace-synthetic"
      secretId="secret-synthetic"
      source={source}
    >
      {(check) => (
        <>
          <SecretReferenceCheckButton state={check.state} onCheck={check.run} />
          <SecretReferenceCheckHint state={check.state} />
        </>
      )}
    </SecretReferenceCheckScope>
  )
}

beforeEach(() => {
  mockCheckReference.mockReset()
})

describe("SecretReferenceCheckButton", () => {
  it("shows the failure on the button and the fix inline", async () => {
    mockCheckReference.mockResolvedValue({
      ok: false,
      error_code: "access_denied",
      message:
        "Reference check failed: access_denied (AWS error code AccessDeniedException)",
    })
    renderCheck()

    fireEvent.click(screen.getByRole("button", { name: "Check access" }))

    expect(
      await screen.findByRole("button", { name: "Access denied" })
    ).toBeInTheDocument()
    expect(screen.queryByText("access_denied")).not.toBeInTheDocument()
    const hint = screen.getByText(/secretsmanager:GetSecretValue/)
    expect(hint).toHaveAttribute(
      "title",
      expect.stringContaining("AccessDeniedException")
    )
  })

  it("shows the hint of the secret's provider", async () => {
    mockCheckReference.mockResolvedValue({
      ok: false,
      error_code: "access_denied",
      message:
        "Reference check failed: access_denied (Bitwarden error code http_403)",
    })
    renderCheck("bitwarden_secrets_manager")

    fireEvent.click(screen.getByRole("button", { name: "Check access" }))

    expect(
      await screen.findByRole("button", { name: "Access denied" })
    ).toBeInTheDocument()
    expect(screen.getByText(/machine account can read/)).toHaveAttribute(
      "title",
      expect.stringContaining("http_403")
    )
    expect(
      screen.queryByText(/secretsmanager:GetSecretValue/)
    ).not.toBeInTheDocument()
  })

  it("checks again from the result button", async () => {
    mockCheckReference
      .mockResolvedValueOnce({ ok: false, error_code: "throttled" })
      .mockResolvedValueOnce({ ok: true, resolved_keys: ["API_TOKEN"] })
    renderCheck()

    fireEvent.click(screen.getByRole("button", { name: "Check access" }))
    fireEvent.click(await screen.findByRole("button", { name: "Rate limited" }))

    expect(
      await screen.findByRole("button", { name: "Reachable" })
    ).toBeInTheDocument()
    expect(screen.queryByText(/throttled the request/)).not.toBeInTheDocument()
    expect(mockCheckReference).toHaveBeenCalledTimes(2)
  })
})
