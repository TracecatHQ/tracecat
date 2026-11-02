import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { BitbucketDataCenterTokenSetup } from "@/components/organization/org-vcs-bitbucket-data-center"

const mockSave = jest.fn()
const mockDelete = jest.fn()
const mockRefetch = jest.fn()
const mockStatus = jest.fn()

jest.mock("@/hooks/use-bitbucket-data-center-credentials", () => ({
  useBitbucketDataCenterTokenCredentialsStatus: () => mockStatus(),
  useBitbucketDataCenterTokenCredentials: () => ({
    saveCredentials: { mutateAsync: mockSave, isPending: false },
  }),
  useDeleteBitbucketDataCenterTokenCredentials: () => ({
    deleteCredentials: { mutateAsync: mockDelete, isPending: false },
  }),
}))
jest.mock("@/components/ui/use-toast", () => ({
  useToast: () => ({ toast: jest.fn() }),
}))

describe("Bitbucket Data Center credentials", () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockSave.mockResolvedValue(undefined)
    mockStatus.mockReturnValue({
      credentialsStatus: { exists: false },
      credentialsStatusIsLoading: false,
      refetchCredentialsStatus: mockRefetch,
    })
  })

  it("saves the instance URL and HTTP access token", async () => {
    const user = userEvent.setup()
    render(<BitbucketDataCenterTokenSetup />)
    await user.click(screen.getByRole("button", { name: "Connect" }))
    await user.type(
      screen.getByLabelText("Instance URL"),
      "https://example.test/bitbucket"
    )
    await user.type(
      screen.getByLabelText("Credential token"),
      "synthetic-token"
    )
    await user.click(screen.getByRole("button", { name: "Save" }))
    await waitFor(() =>
      expect(mockSave).toHaveBeenCalledWith({
        base_url: "https://example.test/bitbucket",
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
    render(<BitbucketDataCenterTokenSetup />)
    expect(
      screen.getByRole("button", { name: "Reconnect" })
    ).toBeInTheDocument()
    expect(
      screen.getByLabelText("Delete Bitbucket Data Center credentials")
    ).toBeInTheDocument()
  })

  it("shows a status failure without claiming the provider is disconnected", () => {
    mockStatus.mockReturnValue({
      credentialsStatusError: new Error("unavailable"),
      refetchCredentialsStatus: mockRefetch,
    })
    render(<BitbucketDataCenterTokenSetup />)
    expect(
      screen.getByText(/Unable to load Bitbucket Data Center credentials/)
    ).toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Connect" })
    ).not.toBeInTheDocument()
  })
})
