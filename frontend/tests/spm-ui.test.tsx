/**
 * @jest-environment jsdom
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import type {
  SpmAssetRead,
  SpmControlRead,
  SpmEndpointRead,
  SpmFindingRead,
} from "@/client"
import {
  SpmAssetsView,
  SpmControlsView,
  SpmEndpointsView,
  SpmFindingsView,
  SpmInstallDrawer,
} from "@/components/spm/spm-ui"

const mockToast = jest.fn()
const mockCreateEndpoint = jest.fn()
const mockDeleteEndpoint = jest.fn()
const mockDecideFinding = jest.fn()
const mockUseEntitlements = jest.fn()
const mockUseSpmActions = jest.fn()
const mockUseSpmAssets = jest.fn()
const mockUseSpmControls = jest.fn()
const mockUseSpmEndpoints = jest.fn()
const mockUseSpmFindings = jest.fn()

jest.mock("@/components/ui/sheet", () => ({
  Sheet: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  SheetContent: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  SheetDescription: ({ children }: { children: ReactNode }) => (
    <p>{children}</p>
  ),
  SheetHeader: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  SheetTitle: ({ children }: { children: ReactNode }) => <h2>{children}</h2>,
}))

jest.mock("@/components/ui/alert-dialog", () => ({
  AlertDialog: ({
    open,
    children,
  }: {
    open: boolean
    onOpenChange?: (open: boolean) => void
    children: ReactNode
  }) => (open ? <div>{children}</div> : null),
  AlertDialogAction: ({
    children,
    disabled,
    onClick,
  }: {
    children: ReactNode
    disabled?: boolean
    onClick?: () => void
  }) => (
    <button type="button" onClick={onClick} disabled={disabled}>
      {children}
    </button>
  ),
  AlertDialogCancel: ({ children }: { children: ReactNode }) => (
    <button type="button">{children}</button>
  ),
  AlertDialogContent: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  AlertDialogDescription: ({ children }: { children: ReactNode }) => (
    <p>{children}</p>
  ),
  AlertDialogFooter: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  AlertDialogHeader: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  AlertDialogTitle: ({ children }: { children: ReactNode }) => (
    <h2>{children}</h2>
  ),
}))

jest.mock("@/components/ui/use-toast", () => ({
  useToast: () => ({
    toast: mockToast,
  }),
}))

jest.mock("@/hooks/use-entitlements", () => ({
  useEntitlements: () => {
    mockUseEntitlements()
    return {
      hasEntitlement: () => true,
      isLoading: false,
    }
  },
}))

jest.mock("@/hooks/use-spm", () => ({
  useSpmActions: () => mockUseSpmActions(),
  useSpmAssets: (params?: unknown) => mockUseSpmAssets(params),
  useSpmControls: () => mockUseSpmControls(),
  useSpmEndpoints: () => mockUseSpmEndpoints(),
  useSpmFindings: (params?: unknown) => mockUseSpmFindings(params),
}))

function paginated<T>(items: T[]) {
  return {
    data: {
      has_more: false,
      items,
      next_cursor: null,
    },
    isLoading: false,
  }
}

function endpoint(id: string, name: string): SpmEndpointRead {
  return {
    client_metadata: {},
    created_at: "2026-04-22T00:00:00Z",
    endpoint_version: "0.1.0",
    enrolled_at: "2026-04-22T00:00:00Z",
    harness: "claude_code",
    home_path: "/Users/chris",
    hostname: `${name.toLowerCase().replaceAll(" ", "-")}.local`,
    id,
    last_seen_at: "2026-04-22T00:00:00Z",
    last_sync_at: "2026-04-22T00:00:00Z",
    last_sync_error: null,
    name,
    organization_id: "org-1",
    os_user: "chris",
    platform: "macos",
    status: "active",
    updated_at: "2026-04-22T00:00:00Z",
  }
}

function pendingEndpoint(id: string, name: string): SpmEndpointRead {
  return {
    ...endpoint(id, name),
    enrolled_at: null,
    last_seen_at: null,
    last_sync_at: null,
    status: "pending",
  }
}

function asset(
  id: string,
  endpointId: string,
  displayName: string,
  assetType: SpmAssetRead["asset_type"]
): SpmAssetRead {
  return {
    asset_class:
      assetType === "claude_md" || assetType === "agents_md"
        ? "instruction_file"
        : "mcp_server",
    asset_type: assetType,
    content_hash: null,
    created_at: "2026-04-22T00:00:00Z",
    display_name: displayName,
    first_seen_at: "2026-04-22T00:00:00Z",
    harness: "claude_code",
    id,
    identity_key: `${endpointId}:${displayName}`,
    last_seen_at: "2026-04-22T00:00:00Z",
    metadata: {
      file_path:
        displayName === "github"
          ? "/Users/chris/.claude.json"
          : `/Users/chris/project/${displayName}`,
    },
    organization_id: "org-1",
    updated_at: "2026-04-22T00:00:00Z",
  }
}

function finding(
  id: string,
  endpointId: string,
  assetId: string,
  assetType: SpmFindingRead["asset_type"],
  overrides: Partial<SpmFindingRead> = {}
): SpmFindingRead {
  return {
    asset_class:
      assetType === "claude_md" || assetType === "agents_md"
        ? "instruction_file"
        : "mcp_server",
    asset_id: assetId,
    asset_sighting_id: null,
    asset_type: assetType,
    closed_at: null,
    control_id:
      assetType === "mcp_server"
        ? "7dca8397-056a-4cc7-a4a6-3fef782b21a2"
        : "4fd32453-138e-4273-8501-bf4809eb7adf",
    control_key:
      assetType === "mcp_server"
        ? "claude.mcp_server.approved"
        : "claude.instruction_file.obfuscation_absent",
    control_revision: "1",
    created_at: "2026-04-22T00:00:00Z",
    endpoint_id: endpointId,
    enrichment: {},
    evidence: {},
    harness: "claude_code",
    id,
    last_decision_at: null,
    opened_at: "2026-04-22T00:00:00Z",
    organization_id: "org-1",
    recommended_action:
      assetType === "agents_md" ? null : "exclude_instruction_file",
    recommended_payload: {},
    severity: "high",
    status: "open",
    summary:
      assetType === "agents_md"
        ? "AGENTS.md should be reviewed"
        : "Instruction file requires enforcement",
    updated_at: "2026-04-22T00:00:00Z",
    ...overrides,
  }
}

function control(
  id: string,
  key: string,
  title: string,
  assetType: SpmControlRead["asset_type"]
): SpmControlRead {
  return {
    action:
      assetType === "mcp_server"
        ? "disable_mcp_server"
        : "exclude_instruction_file",
    asset_class: assetType === "mcp_server" ? "mcp_server" : "instruction_file",
    asset_type: assetType,
    aliases: [],
    description: `${title} description`,
    harness: "claude_code",
    id,
    key,
    revision: "1",
    severity: "high",
    title,
  }
}

describe("SPM operator UI", () => {
  const endpoints = [
    endpoint("endpoint-1", "Chris MacBook"),
    endpoint("endpoint-2", "CI Mac Mini"),
  ]
  const pendingEndpoints = [
    pendingEndpoint("endpoint-pending", "Pending MacBook"),
    endpoint("endpoint-active", "Active MacBook"),
  ]
  const assets = [
    asset("asset-1", "endpoint-1", "github", "mcp_server"),
    asset("asset-2", "endpoint-2", "CLAUDE.md", "claude_md"),
  ]

  beforeEach(() => {
    jest.clearAllMocks()
    mockUseSpmActions.mockReturnValue({
      createEndpoint: {
        isPending: false,
        mutateAsync: mockCreateEndpoint,
      },
      deleteEndpoint: {
        isPending: false,
        mutateAsync: mockDeleteEndpoint,
      },
      decideFinding: {
        isPending: false,
        mutateAsync: mockDecideFinding,
      },
    })
    mockDeleteEndpoint.mockResolvedValue(undefined)
    mockUseSpmEndpoints.mockReturnValue(paginated(endpoints))
    mockUseSpmAssets.mockImplementation((params?: { endpointId?: string }) => {
      if (params?.endpointId) {
        const filteredAssets =
          params.endpointId === "endpoint-2" ? [assets[1]] : [assets[0]]
        return paginated(filteredAssets)
      }
      return paginated(assets)
    })
    mockUseSpmControls.mockReturnValue({
      data: [
        control(
          "7dca8397-056a-4cc7-a4a6-3fef782b21a2",
          "claude.mcp_server.approved",
          "Approved MCP servers",
          "mcp_server"
        ),
        control(
          "4fd32453-138e-4273-8501-bf4809eb7adf",
          "claude.instruction_file.obfuscation_absent",
          "Instruction files must not be obfuscated",
          "claude_md"
        ),
      ],
      isLoading: false,
    })
    mockUseSpmFindings.mockImplementation(
      (params?: { controlId?: string; endpointId?: string }) => {
        const findings = [
          finding("finding-1", "endpoint-1", "asset-1", "mcp_server", {
            recommended_action: "disable_mcp_server",
            summary: "Github MCP server is not approved",
          }),
          finding("finding-2", "endpoint-2", "asset-2", "claude_md", {
            summary: "CLAUDE.md should be excluded",
          }),
          finding("finding-3", "endpoint-2", "asset-2", "agents_md", {
            summary: "AGENTS.md is inventory only",
          }),
        ]
        const filteredFindings = findings.filter((item) => {
          if (params?.controlId && item.control_id !== params.controlId) {
            return false
          }
          if (params?.endpointId && item.endpoint_id !== params.endpointId) {
            return false
          }
          return true
        })
        return paginated(filteredFindings)
      }
    )
  })

  it("filters assets by endpoint name and passes the resolved endpoint id", async () => {
    render(<SpmAssetsView />)

    fireEvent.change(screen.getByLabelText("Filter by endpoint"), {
      target: { value: "endpoint-2" },
    })

    await waitFor(() => {
      expect(mockUseSpmAssets).toHaveBeenLastCalledWith(
        expect.objectContaining({ endpointId: "endpoint-2" })
      )
    })
    expect(screen.getAllByText("CLAUDE.md").length).toBeGreaterThan(0)
  })

  it("shows control detail drill-in for the selected control", async () => {
    render(<SpmControlsView />)

    fireEvent.click(
      screen.getByRole("button", {
        name: /Instruction files must not be obfuscated/i,
      })
    )

    await waitFor(() => {
      expect(
        screen.getByText("Instruction files must not be obfuscated description")
      ).toBeInTheDocument()
    })
    expect(screen.getByText("CLAUDE.md should be excluded")).toBeInTheDocument()
  })

  it("shows enforceable and inventory-only finding action states", () => {
    render(<SpmFindingsView />)

    expect(screen.queryByText("Queued")).not.toBeInTheDocument()
    expect(screen.getAllByRole("button", { name: "Enforce" })[0]).toBeEnabled()
    expect(screen.getAllByRole("button", { name: "Enforce" })[2]).toBeDisabled()
    expect(screen.getByText("Inventory only")).toBeInTheDocument()
  })

  it("filters findings by status and resets the feed filters", async () => {
    render(<SpmFindingsView />)

    fireEvent.change(screen.getByLabelText("Filter by status"), {
      target: { value: "resolved" },
    })

    await waitFor(() => {
      expect(screen.getByText("No matching records.")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole("button", { name: /Reset/i }))

    await waitFor(() => {
      expect(
        screen.getByText("Github MCP server is not approved")
      ).toBeInTheDocument()
    })
  })

  it("shows install commands after a successful endpoint enrollment", async () => {
    mockCreateEndpoint.mockResolvedValue({
      endpoint: endpoints[0],
      enrollment_token: "tcspm_enroll_example",
    })

    render(<SpmInstallDrawer />)

    fireEvent.click(
      screen.getByRole("button", { name: "Create endpoint enrollment" })
    )

    await waitFor(() => {
      expect(screen.getByText("Install command")).toBeInTheDocument()
    })
    expect(mockToast).toHaveBeenCalledWith(
      expect.objectContaining({ title: "Endpoint created" })
    )
  })

  it("surfaces install drawer errors from endpoint creation", async () => {
    mockCreateEndpoint.mockRejectedValue(
      Object.assign(new Error("request failed"), {
        body: { detail: "token issuance failed" },
      })
    )

    render(<SpmInstallDrawer />)

    fireEvent.click(
      screen.getByRole("button", { name: "Create endpoint enrollment" })
    )

    await waitFor(() => {
      expect(mockToast).toHaveBeenCalledWith(
        expect.objectContaining({
          description: "token issuance failed",
          title: "Create endpoint failed",
          variant: "destructive",
        })
      )
    })
  })

  it("shows cancel enrollment only for pure pending endpoints", () => {
    mockUseSpmEndpoints.mockReturnValue(paginated(pendingEndpoints))

    render(<SpmEndpointsView />)

    expect(
      screen.getAllByRole("button", { name: "Cancel enrollment" })
    ).toHaveLength(1)
    expect(screen.getByText("Pending MacBook")).toBeInTheDocument()
    expect(screen.getByText("Active MacBook")).toBeInTheDocument()
    expect(screen.queryAllByRole("link")).toHaveLength(0)
  })

  it("keeps endpoint headers visible while endpoints are loading", () => {
    mockUseSpmEndpoints.mockReturnValue({
      data: undefined,
      isError: false,
      isFetching: true,
      isLoading: true,
    })

    render(<SpmEndpointsView />)

    expect(screen.getByText("Endpoints")).toBeInTheDocument()
    expect(
      screen.getByPlaceholderText("Search endpoints...")
    ).toBeInTheDocument()
    expect(screen.getByLabelText("Filter by status")).toBeInTheDocument()
    expect(screen.getByLabelText("Filter by compliance")).toBeInTheDocument()
    expect(screen.getByLabelText("Filter by sync")).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: "Install endpoint" })
    ).toBeInTheDocument()
    expect(screen.getAllByText("Loading endpoints...").length).toBeGreaterThan(
      0
    )
    expect(screen.queryByText("No endpoints yet")).not.toBeInTheDocument()
  })

  it("shows the endpoint header status with the endpoint poll cadence", () => {
    render(<SpmEndpointsView />)

    expect(
      screen.getByText("2 endpoints · Poll every 5 minutes")
    ).toBeInTheDocument()
  })

  it("shows refreshing endpoint header status while keeping cached rows", () => {
    mockUseSpmEndpoints.mockReturnValue({
      ...paginated(endpoints),
      isFetching: true,
    })

    render(<SpmEndpointsView />)

    expect(screen.getByText("2 endpoints · Refreshing...")).toBeInTheDocument()
    expect(screen.getByText("Chris MacBook")).toBeInTheDocument()
    expect(screen.getByText("CI Mac Mini")).toBeInTheDocument()
  })

  it("keeps endpoint headers visible while findings are loading", () => {
    mockUseSpmFindings.mockReturnValue({
      data: undefined,
      isError: false,
      isLoading: true,
    })

    render(<SpmEndpointsView />)

    expect(screen.getByText("Endpoints")).toBeInTheDocument()
    expect(
      screen.getByPlaceholderText("Search endpoints...")
    ).toBeInTheDocument()
    expect(screen.getByText("Chris MacBook")).toBeInTheDocument()
    expect(screen.getAllByText("Checking findings").length).toBeGreaterThan(0)
  })

  it("keeps endpoint headers visible when findings fail to load", () => {
    mockUseSpmFindings.mockReturnValue({
      data: undefined,
      error: Object.assign(new Error("request failed"), {
        body: { detail: "database unavailable" },
      }),
      isError: true,
      isLoading: false,
    })

    render(<SpmEndpointsView />)

    expect(screen.getByText("Endpoints")).toBeInTheDocument()
    expect(
      screen.getByPlaceholderText("Search endpoints...")
    ).toBeInTheDocument()
    expect(screen.getByText("Chris MacBook")).toBeInTheDocument()
    expect(screen.getAllByText("Findings unavailable").length).toBeGreaterThan(
      0
    )
    expect(screen.getByText(/database unavailable/)).toBeInTheDocument()
  })

  it("cancels a pending enrollment after confirmation", async () => {
    mockUseSpmEndpoints.mockReturnValue(paginated(pendingEndpoints))

    render(<SpmEndpointsView />)

    fireEvent.click(screen.getByRole("button", { name: "Cancel enrollment" }))

    await waitFor(() => {
      expect(screen.getByText("Cancel pending enrollment")).toBeInTheDocument()
    })

    fireEvent.click(
      screen.getAllByRole("button", { name: "Cancel enrollment" })[1]
    )

    await waitFor(() => {
      expect(mockDeleteEndpoint).toHaveBeenCalledWith({
        endpointId: "endpoint-pending",
      })
    })
    expect(mockToast).toHaveBeenCalledWith(
      expect.objectContaining({ title: "Enrollment canceled" })
    )
  })
})
