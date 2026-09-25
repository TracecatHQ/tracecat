import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { BitbucketTokenSetup } from "@/components/organization/org-vcs-bitbucket"

const mockSave = jest.fn()
const mockDelete = jest.fn()
const mockRefetch = jest.fn()
const mockStatus = jest.fn()

jest.mock("@/hooks/use-bitbucket-credentials", () => ({
  useBitbucketTokenCredentialsStatus: () => mockStatus(),
  useBitbucketTokenCredentials: () => ({
    saveCredentials: { mutateAsync: mockSave, isPending: false },
  }),
  useDeleteBitbucketTokenCredentials: () => ({
    deleteCredentials: { mutateAsync: mockDelete, isPending: false },
  }),
}))
jest.mock("@/components/ui/use-toast", () => ({
  useToast: () => ({ toast: jest.fn() }),
}))

describe("Bitbucket Cloud credentials", () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockSave.mockResolvedValue(undefined)
    mockStatus.mockReturnValue({
      credentialsStatus: { exists: false },
      credentialsStatusIsLoading: false,
      refetchCredentialsStatus: mockRefetch,
    })
  })

  it("saves the account email and API token", async () => {
    const user = userEvent.setup()
    render(<BitbucketTokenSetup />)
    await user.click(screen.getByRole("button", { name: "Connect" }))
    await user.type(
      screen.getByLabelText("Account email"),
      "example@example.com"
    )
    await user.type(
      screen.getByLabelText("Credential token"),
      "synthetic-token"
    )
    await user.click(screen.getByRole("button", { name: "Save" }))
    await waitFor(() =>
      expect(mockSave).toHaveBeenCalledWith({
        email: "example@example.com",
        token: "synthetic-token",
      })
    )
    expect(mockRefetch).toHaveBeenCalled()
  })

  it("offers reconnect for corrupt credentials", async () => {
    mockStatus.mockReturnValue({
      credentialsStatus: { exists: true, is_corrupted: true },
      refetchCredentialsStatus: mockRefetch,
    })
    render(<BitbucketTokenSetup />)
    expect(
      screen.getByRole("button", { name: "Reconnect" })
    ).toBeInTheDocument()
    expect(
      screen.getByLabelText("Delete Bitbucket credentials")
    ).toBeInTheDocument()
  })

  it("shows a status failure without claiming the provider is disconnected", () => {
    mockStatus.mockReturnValue({
      credentialsStatusError: new Error("unavailable"),
      refetchCredentialsStatus: mockRefetch,
    })
    render(<BitbucketTokenSetup />)
    expect(
      screen.getByText(/Unable to load Bitbucket credentials/)
    ).toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Connect" })
    ).not.toBeInTheDocument()
  })
})
