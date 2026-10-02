import { fireEvent, render, screen } from "@testing-library/react"
import { AwsSecretReferenceCheckButton } from "@/components/workspaces/aws-secret-reference-check-button"

const mockCheckReference = jest.fn()

jest.mock("@/hooks/use-secret-stores", () => ({
  describeApiError: () => "API error",
  useAwsSecretReferences: () => ({
    checkReference: mockCheckReference,
    checkReferencePending: false,
  }),
}))

describe("AwsSecretReferenceCheckButton", () => {
  it("renders a readable label and hint for a failure code", async () => {
    mockCheckReference.mockResolvedValue({
      ok: false,
      error_code: "access_denied",
      message:
        "Reference check failed: access_denied (AWS error code AccessDeniedException)",
    })
    render(
      <AwsSecretReferenceCheckButton
        workspaceId="workspace-synthetic"
        secretId="secret-synthetic"
      />
    )

    fireEvent.click(screen.getByRole("button", { name: "Check" }))

    const status = await screen.findByText("Access denied")
    expect(screen.queryByText("access_denied")).not.toBeInTheDocument()
    expect(status).toHaveAttribute(
      "title",
      expect.stringContaining("secretsmanager:GetSecretValue")
    )
    expect(status).toHaveAttribute(
      "title",
      expect.stringContaining("AccessDeniedException")
    )
  })
})
