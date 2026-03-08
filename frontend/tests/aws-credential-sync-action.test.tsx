import { fireEvent, render, screen } from "@testing-library/react"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { AwsCredentialSyncAction } from "@/components/secrets/aws-credential-sync-action"

jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: jest.fn(),
}))

jest.mock("@/components/secrets/aws-credential-sync-dialog", () => ({
  AwsCredentialSyncDialog: ({
    open,
  }: {
    open: boolean
    onOpenChange: (open: boolean) => void
  }) => (open ? <div>AWS sync dialog</div> : null),
}))

const mockUseScopeCheck = jest.mocked(useScopeCheck)

describe("AwsCredentialSyncAction", () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it("hides the action when the user lacks the sync scope", () => {
    mockUseScopeCheck.mockReturnValue(false)

    render(<AwsCredentialSyncAction />)

    expect(
      screen.queryByRole("button", { name: "AWS sync" })
    ).not.toBeInTheDocument()
  })

  it("shows the action and opens the dialog when clicked", () => {
    mockUseScopeCheck.mockReturnValue(true)

    render(<AwsCredentialSyncAction />)

    fireEvent.click(screen.getByRole("button", { name: "AWS sync" }))

    expect(screen.getByText("AWS sync dialog")).toBeInTheDocument()
  })
})
