import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { BitbucketSettings } from "@/components/organization/org-vcs-bitbucket-settings"

const mockCloud = jest.fn()
const mockDataCenter = jest.fn()
jest.mock("@/hooks/use-bitbucket-credentials", () => ({
  useBitbucketTokenCredentialsStatus: () => mockCloud(),
}))
jest.mock("@/hooks/use-bitbucket-data-center-credentials", () => ({
  useBitbucketDataCenterTokenCredentialsStatus: () => mockDataCenter(),
}))
jest.mock("@/components/organization/org-vcs-bitbucket", () => ({
  BitbucketTokenSetup: () => <div>Cloud connection controls</div>,
}))
jest.mock("@/components/organization/org-vcs-bitbucket-data-center", () => ({
  BitbucketDataCenterTokenSetup: () => (
    <div>Data Center connection controls</div>
  ),
}))

describe("Bitbucket provider overview", () => {
  beforeEach(() => {
    mockCloud.mockReturnValue({ credentialsStatus: { exists: false } })
    mockDataCenter.mockReturnValue({ credentialsStatus: { exists: false } })
  })

  it("shows one provider and reveals both connection types only on demand", async () => {
    const user = userEvent.setup()
    render(<BitbucketSettings />)
    expect(screen.getByText("Bitbucket")).toBeInTheDocument()
    expect(
      screen.queryByText("Cloud connection controls")
    ).not.toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Connect" }))
    expect(screen.getByText("Cloud connection controls")).toBeInTheDocument()
    expect(
      screen.getByText("Data Center connection controls")
    ).toBeInTheDocument()
  })

  it.each(["cloud", "data-center"])(
    "recognizes a configured %s connection",
    (provider) => {
      const mock = provider === "cloud" ? mockCloud : mockDataCenter
      mock.mockReturnValue({ credentialsStatus: { exists: true } })
      render(<BitbucketSettings />)
      expect(screen.getByText("Connected")).toBeInTheDocument()
      expect(screen.getByRole("button", { name: "Manage" })).toBeInTheDocument()
    }
  )

  it("does not hide a failed status behind the other connected provider", () => {
    mockCloud.mockReturnValue({ credentialsStatus: { exists: true } })
    mockDataCenter.mockReturnValue({
      credentialsStatusError: new Error("unavailable"),
    })
    render(<BitbucketSettings />)
    expect(
      screen.getByText("Unable to load all connections")
    ).toBeInTheDocument()
    expect(screen.queryByText("Connected")).not.toBeInTheDocument()
  })
})
