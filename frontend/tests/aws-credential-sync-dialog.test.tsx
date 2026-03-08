import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import type { CredentialSyncResult } from "@/client"
import { AwsCredentialSyncDialog } from "@/components/secrets/aws-credential-sync-dialog"
import { useWorkspaceDetails } from "@/hooks/use-workspace"
import { useAwsCredentialSync } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

jest.mock("@/hooks/use-workspace", () => ({
  useWorkspaceDetails: jest.fn(),
}))

jest.mock("@/lib/hooks", () => ({
  useAwsCredentialSync: jest.fn(),
}))

jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: jest.fn(),
}))

jest.mock("@/components/ui/dialog", () => ({
  Dialog: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  DialogContent: ({
    children,
  }: {
    children: ReactNode
    className?: string
  }) => <div>{children}</div>,
  DialogHeader: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  DialogTitle: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  DialogDescription: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
}))

const mockUseWorkspaceDetails = jest.mocked(useWorkspaceDetails)
const mockUseAwsCredentialSync = jest.mocked(useAwsCredentialSync)
const mockUseWorkspaceId = jest.mocked(useWorkspaceId)

describe("AwsCredentialSyncDialog", () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockUseWorkspaceId.mockReturnValue("workspace-123")
    mockUseWorkspaceDetails.mockReturnValue({
      workspace: { name: "Security operations" },
      workspaceLoading: false,
      workspaceError: null,
    } as ReturnType<typeof useWorkspaceDetails>)
  })

  it("saves organization AWS settings with the expected payload", async () => {
    const updateAwsCredentialSyncConfig = jest
      .fn()
      .mockResolvedValue(undefined) as jest.MockedFunction<
      ReturnType<typeof useAwsCredentialSync>["updateAwsCredentialSyncConfig"]
    >
    const refetchAwsCredentialSyncConfig = jest
      .fn()
      .mockResolvedValue(undefined) as jest.MockedFunction<
      ReturnType<typeof useAwsCredentialSync>["refetchAwsCredentialSyncConfig"]
    >

    mockUseAwsCredentialSync.mockReturnValue({
      awsCredentialSyncConfig: {
        region: null,
        secret_prefix: null,
        has_access_key_id: false,
        has_secret_access_key: false,
        has_session_token: false,
        is_configured: false,
        is_corrupted: false,
      },
      awsCredentialSyncConfigIsLoading: false,
      awsCredentialSyncConfigError: null,
      refetchAwsCredentialSyncConfig,
      updateAwsCredentialSyncConfig,
      isUpdatingAwsCredentialSyncConfig: false,
      pushAwsCredentialSync: jest.fn(),
      isPushingAwsCredentialSync: false,
      pullAwsCredentialSync: jest.fn(),
      isPullingAwsCredentialSync: false,
    })

    render(<AwsCredentialSyncDialog open onOpenChange={jest.fn()} />)

    fireEvent.change(screen.getByLabelText("Region"), {
      target: { value: "us-east-1" },
    })
    fireEvent.change(screen.getByLabelText("Secret prefix"), {
      target: { value: "tracecat/credentials" },
    })
    fireEvent.change(screen.getByLabelText("Access key ID"), {
      target: { value: "AKIA_TEST" },
    })
    fireEvent.change(screen.getByLabelText("Secret access key"), {
      target: { value: "secret-test-key" },
    })

    fireEvent.click(screen.getByRole("button", { name: "Save settings" }))

    await waitFor(() => {
      expect(updateAwsCredentialSyncConfig).toHaveBeenCalledWith({
        region: "us-east-1",
        secret_prefix: "tracecat/credentials",
        access_key_id: "AKIA_TEST",
        secret_access_key: "secret-test-key",
      })
    })
    expect(refetchAwsCredentialSyncConfig).toHaveBeenCalled()
  })

  it("runs push and pull and renders the last result", async () => {
    const pullResult: CredentialSyncResult = {
      provider: "aws",
      operation: "pull",
      success: true,
      processed: 2,
      created: 1,
      updated: 1,
      skipped: 0,
      failed: 0,
      errors: [],
    }
    const pushAwsCredentialSync = jest.fn().mockResolvedValue({
      provider: "aws",
      operation: "push",
      success: true,
      processed: 2,
      created: 2,
      updated: 0,
      skipped: 0,
      failed: 0,
      errors: [],
    }) as jest.MockedFunction<
      ReturnType<typeof useAwsCredentialSync>["pushAwsCredentialSync"]
    >
    const pullAwsCredentialSync = jest
      .fn()
      .mockResolvedValue(pullResult) as jest.MockedFunction<
      ReturnType<typeof useAwsCredentialSync>["pullAwsCredentialSync"]
    >

    mockUseAwsCredentialSync.mockReturnValue({
      awsCredentialSyncConfig: {
        region: "us-east-1",
        secret_prefix: "tracecat/credentials",
        has_access_key_id: true,
        has_secret_access_key: true,
        has_session_token: false,
        is_configured: true,
        is_corrupted: false,
      },
      awsCredentialSyncConfigIsLoading: false,
      awsCredentialSyncConfigError: null,
      refetchAwsCredentialSyncConfig: jest.fn(),
      updateAwsCredentialSyncConfig: jest.fn(),
      isUpdatingAwsCredentialSyncConfig: false,
      pushAwsCredentialSync,
      isPushingAwsCredentialSync: false,
      pullAwsCredentialSync,
      isPullingAwsCredentialSync: false,
    })

    render(<AwsCredentialSyncDialog open onOpenChange={jest.fn()} />)

    fireEvent.click(screen.getByRole("button", { name: "Push all to AWS" }))
    fireEvent.click(screen.getByRole("button", { name: "Pull all from AWS" }))

    await waitFor(() => {
      expect(pushAwsCredentialSync).toHaveBeenCalled()
      expect(pullAwsCredentialSync).toHaveBeenCalled()
    })

    expect(screen.getByText("Last pull result")).toBeInTheDocument()
    expect(
      screen.getByText("2 processed • 1 created • 1 updated")
    ).toBeInTheDocument()
  })

  it("shows an inline error and keeps secret inputs populated when save fails", async () => {
    const updateAwsCredentialSyncConfig = jest
      .fn()
      .mockRejectedValue(
        new Error("Could not update the organization AWS sync settings.")
      ) as jest.MockedFunction<
      ReturnType<typeof useAwsCredentialSync>["updateAwsCredentialSyncConfig"]
    >
    const refetchAwsCredentialSyncConfig = jest
      .fn()
      .mockResolvedValue(undefined) as jest.MockedFunction<
      ReturnType<typeof useAwsCredentialSync>["refetchAwsCredentialSyncConfig"]
    >

    mockUseAwsCredentialSync.mockReturnValue({
      awsCredentialSyncConfig: {
        region: "us-east-1",
        secret_prefix: "tracecat/credentials",
        has_access_key_id: true,
        has_secret_access_key: true,
        has_session_token: false,
        is_configured: true,
        is_corrupted: false,
      },
      awsCredentialSyncConfigIsLoading: false,
      awsCredentialSyncConfigError: null,
      refetchAwsCredentialSyncConfig,
      updateAwsCredentialSyncConfig,
      isUpdatingAwsCredentialSyncConfig: false,
      pushAwsCredentialSync: jest.fn(),
      isPushingAwsCredentialSync: false,
      pullAwsCredentialSync: jest.fn(),
      isPullingAwsCredentialSync: false,
    })

    render(<AwsCredentialSyncDialog open onOpenChange={jest.fn()} />)

    fireEvent.change(screen.getByLabelText("Access key ID"), {
      target: { value: "AKIA_RETRY" },
    })
    fireEvent.change(screen.getByLabelText("Secret access key"), {
      target: { value: "retry-secret" },
    })

    fireEvent.click(screen.getByRole("button", { name: "Save settings" }))

    await waitFor(() => {
      expect(screen.getByText("Unable to save settings")).toBeInTheDocument()
    })

    expect(refetchAwsCredentialSyncConfig).not.toHaveBeenCalled()
    expect(screen.getByDisplayValue("AKIA_RETRY")).toBeInTheDocument()
    expect(screen.getByDisplayValue("retry-secret")).toBeInTheDocument()
  })

  it("does not clear a stored session token unless the field was touched", async () => {
    const updateAwsCredentialSyncConfig = jest
      .fn()
      .mockResolvedValue(undefined) as jest.MockedFunction<
      ReturnType<typeof useAwsCredentialSync>["updateAwsCredentialSyncConfig"]
    >

    mockUseAwsCredentialSync.mockReturnValue({
      awsCredentialSyncConfig: {
        region: "us-east-1",
        secret_prefix: "tracecat/credentials",
        has_access_key_id: true,
        has_secret_access_key: true,
        has_session_token: true,
        is_configured: true,
        is_corrupted: false,
      },
      awsCredentialSyncConfigIsLoading: false,
      awsCredentialSyncConfigError: null,
      refetchAwsCredentialSyncConfig: jest.fn().mockResolvedValue(undefined),
      updateAwsCredentialSyncConfig,
      isUpdatingAwsCredentialSyncConfig: false,
      pushAwsCredentialSync: jest.fn(),
      isPushingAwsCredentialSync: false,
      pullAwsCredentialSync: jest.fn(),
      isPullingAwsCredentialSync: false,
    })

    render(<AwsCredentialSyncDialog open onOpenChange={jest.fn()} />)

    fireEvent.change(screen.getByLabelText("Region"), {
      target: { value: "us-west-2" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }))

    await waitFor(() => {
      expect(updateAwsCredentialSyncConfig).toHaveBeenCalledWith({
        region: "us-west-2",
        secret_prefix: "tracecat/credentials",
      })
    })
  })

  it("clears a stored session token when the field is explicitly emptied", async () => {
    const updateAwsCredentialSyncConfig = jest
      .fn()
      .mockResolvedValue(undefined) as jest.MockedFunction<
      ReturnType<typeof useAwsCredentialSync>["updateAwsCredentialSyncConfig"]
    >

    mockUseAwsCredentialSync.mockReturnValue({
      awsCredentialSyncConfig: {
        region: "us-east-1",
        secret_prefix: "tracecat/credentials",
        has_access_key_id: true,
        has_secret_access_key: true,
        has_session_token: true,
        is_configured: true,
        is_corrupted: false,
      },
      awsCredentialSyncConfigIsLoading: false,
      awsCredentialSyncConfigError: null,
      refetchAwsCredentialSyncConfig: jest.fn().mockResolvedValue(undefined),
      updateAwsCredentialSyncConfig,
      isUpdatingAwsCredentialSyncConfig: false,
      pushAwsCredentialSync: jest.fn(),
      isPushingAwsCredentialSync: false,
      pullAwsCredentialSync: jest.fn(),
      isPullingAwsCredentialSync: false,
    })

    render(<AwsCredentialSyncDialog open onOpenChange={jest.fn()} />)

    const sessionTokenInput = screen.getByLabelText(/Session token/i)
    fireEvent.change(sessionTokenInput, {
      target: { value: "temporary-token" },
    })
    fireEvent.change(sessionTokenInput, {
      target: { value: "" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }))

    await waitFor(() => {
      expect(updateAwsCredentialSyncConfig).toHaveBeenCalledWith({
        region: "us-east-1",
        secret_prefix: "tracecat/credentials",
        session_token: null,
      })
    })
  })

  it("shows inline errors when push or pull fails", async () => {
    const pushAwsCredentialSync = jest
      .fn()
      .mockRejectedValue(
        new Error("Could not push credentials to AWS Secrets Manager.")
      ) as jest.MockedFunction<
      ReturnType<typeof useAwsCredentialSync>["pushAwsCredentialSync"]
    >
    const pullAwsCredentialSync = jest
      .fn()
      .mockRejectedValue(
        new Error("Could not pull credentials from AWS Secrets Manager.")
      ) as jest.MockedFunction<
      ReturnType<typeof useAwsCredentialSync>["pullAwsCredentialSync"]
    >

    mockUseAwsCredentialSync.mockReturnValue({
      awsCredentialSyncConfig: {
        region: "us-east-1",
        secret_prefix: "tracecat/credentials",
        has_access_key_id: true,
        has_secret_access_key: true,
        has_session_token: false,
        is_configured: true,
        is_corrupted: false,
      },
      awsCredentialSyncConfigIsLoading: false,
      awsCredentialSyncConfigError: null,
      refetchAwsCredentialSyncConfig: jest.fn(),
      updateAwsCredentialSyncConfig: jest.fn(),
      isUpdatingAwsCredentialSyncConfig: false,
      pushAwsCredentialSync,
      isPushingAwsCredentialSync: false,
      pullAwsCredentialSync,
      isPullingAwsCredentialSync: false,
    })

    render(<AwsCredentialSyncDialog open onOpenChange={jest.fn()} />)

    fireEvent.click(screen.getByRole("button", { name: "Push all to AWS" }))

    await waitFor(() => {
      expect(screen.getByText("Unable to push credentials")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole("button", { name: "Pull all from AWS" }))

    await waitFor(() => {
      expect(screen.getByText("Unable to pull credentials")).toBeInTheDocument()
    })
  })
})
