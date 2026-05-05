/**
 * @jest-environment jsdom
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import type {
  SpmControlRead,
  SpmEndpointInventoryItemRead,
  SpmEndpointRead,
  SpmFindingRead,
  SpmInventoryItemRead,
  SpmInventoryItemType,
  SpmInventorySourceType,
  SpmResponseActionRead,
} from "@/client"
import {
  SpmControlsView,
  SpmEndpointsView,
  SpmFindingsView,
  SpmInstallDrawer,
  SpmInventoryView,
  SpmResponseActionsView,
} from "@/components/spm/spm-ui"

const mockToast = jest.fn()
const mockCreateEndpoint = jest.fn()
const mockCreateResponseActionPreview = jest.fn()
const mockDeleteEndpoint = jest.fn()
const mockDecideFinding = jest.fn()
const mockUseEntitlements = jest.fn()
const mockUseSpmActions = jest.fn()
const mockUseSpmInventory = jest.fn()
const mockUseSpmControls = jest.fn()
const mockUseSpmEndpointInventoryForEndpoints = jest.fn()
const mockUseSpmEndpoints = jest.fn()
const mockUseSpmFindings = jest.fn()
const mockUseSpmInventoryTaxonomy = jest.fn()
const mockUseSpmResponseActionPreview = jest.fn()
const mockUseSpmResponseActions = jest.fn()

jest.mock("react-diff-viewer-continued", () => {
  function MockReactDiffViewer({
    newValue,
    oldValue,
  }: {
    newValue: string
    oldValue: string
  }) {
    return (
      <div data-testid="mock-react-diff-viewer">
        <pre>{oldValue}</pre>
        <pre>{newValue}</pre>
      </div>
    )
  }
  return {
    __esModule: true,
    default: MockReactDiffViewer,
    DiffMethod: {
      WORDS_WITH_SPACE: "WORDS_WITH_SPACE",
    },
  }
})

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
  useSpmInventory: (params?: unknown) => mockUseSpmInventory(params),
  useSpmControls: () => mockUseSpmControls(),
  useSpmEndpointInventoryForEndpoints: (endpointIds: string[]) =>
    mockUseSpmEndpointInventoryForEndpoints(endpointIds),
  useSpmEndpoints: () => mockUseSpmEndpoints(),
  useSpmFindings: (params?: unknown) => mockUseSpmFindings(params),
  useSpmInventoryTaxonomy: () => mockUseSpmInventoryTaxonomy(),
  useSpmResponseActionPreview: (params?: unknown) =>
    mockUseSpmResponseActionPreview(params),
  useSpmResponseActions: () => mockUseSpmResponseActions(),
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

function endpoint(
  id: string,
  name: string,
  overrides: Partial<SpmEndpointRead> = {}
): SpmEndpointRead {
  return {
    client_metadata: {},
    compliance_status: "compliant",
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
    ...overrides,
  }
}

function pendingEndpoint(id: string, name: string): SpmEndpointRead {
  return {
    ...endpoint(id, name),
    compliance_status: "not_assessed",
    enrolled_at: null,
    last_seen_at: null,
    last_sync_at: null,
    status: "pending",
  }
}

function inventoryItem(
  id: string,
  endpointId: string,
  displayName: string,
  itemType: SpmInventoryItemType,
  sourceType: SpmInventorySourceType
): SpmInventoryItemRead {
  const sourceLocation =
    displayName === "github"
      ? "/Users/chris/.claude.json"
      : `/Users/chris/project/${displayName}`
  return {
    item_type: itemType,
    item_location: sourceLocation,
    source_location: sourceLocation,
    source_type: sourceType,
    content_hash: null,
    created_at: "2026-04-22T00:00:00Z",
    display_name: displayName,
    first_seen_at: "2026-04-22T00:00:00Z",
    harness: "claude_code",
    id,
    identity_key: `${endpointId}:${displayName}`,
    last_seen_at: "2026-04-22T00:00:00Z",
    metadata: {
      file_path: sourceLocation,
    },
    organization_id: "org-1",
    updated_at: "2026-04-22T00:00:00Z",
  }
}

function endpointInventoryItem(
  item: SpmInventoryItemRead,
  endpointId: string
): SpmEndpointInventoryItemRead {
  return {
    inventory_item_id: item.id,
    inventory_observation_id: `${endpointId}:${item.id}`,
    item_type: item.item_type,
    item_location: item.item_location,
    source_location: item.source_location,
    source_type: item.source_type,
    content_hash: item.content_hash,
    display_name: item.display_name,
    endpoint_id: endpointId,
    evidence: {},
    first_seen_at: item.first_seen_at,
    harness: item.harness,
    identity_key: item.identity_key,
    last_seen_at: item.last_seen_at,
    metadata: item.metadata,
    observed_state: {},
    organization_id: item.organization_id,
    workspace_id: null,
  }
}

function finding(
  id: string,
  endpointId: string,
  inventoryItemId: string,
  itemType: SpmInventoryItemType,
  sourceType: SpmInventorySourceType,
  overrides: Partial<SpmFindingRead> = {}
): SpmFindingRead {
  return {
    inventory_item_id: inventoryItemId,
    inventory_observation_id: null,
    item_type: itemType,
    item_location:
      sourceType === "claude_json"
        ? "/Users/chris/.claude.json"
        : `/Users/chris/project/${sourceType}`,
    source_location:
      sourceType === "claude_json"
        ? "/Users/chris/.claude.json"
        : `/Users/chris/project/${sourceType}`,
    source_type: sourceType,
    closed_at: null,
    control_id:
      itemType === "mcp_server"
        ? "7dca8397-056a-4cc7-a4a6-3fef782b21a2"
        : "4fd32453-138e-4273-8501-bf4809eb7adf",
    control_key:
      itemType === "mcp_server"
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
      sourceType === "agents_md" ? null : "exclude_instruction_file",
    recommended_payload: {},
    severity: "high",
    status: "open",
    summary:
      sourceType === "agents_md"
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
  itemType: SpmInventoryItemType
): SpmControlRead {
  return {
    action:
      itemType === "mcp_server"
        ? "disable_mcp_server"
        : "exclude_instruction_file",
    item_type: itemType,
    source_types:
      itemType === "instruction_file" ? ["claude_md", "claude_local_md"] : [],
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

function responseAction(
  key: SpmResponseActionRead["key"],
  title: string,
  itemTypes: SpmInventoryItemType[]
): SpmResponseActionRead {
  return {
    description: `${title} description`,
    disruptive: true,
    execution_mode: "endpoint_sync",
    harness: "claude_code",
    item_types: itemTypes,
    key,
    payload_fields: ["target_path"],
    preview_supported: true,
    target_surface: "writable_claude_settings",
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
  const inventoryItems = [
    inventoryItem(
      "item-1",
      "endpoint-1",
      "github",
      "mcp_server",
      "claude_json"
    ),
    inventoryItem(
      "item-2",
      "endpoint-2",
      "CLAUDE.md",
      "instruction_file",
      "claude_md"
    ),
    inventoryItem(
      "item-3",
      "endpoint-2",
      "AGENTS.md",
      "instruction_file",
      "agents_md"
    ),
  ]

  beforeEach(() => {
    jest.clearAllMocks()
    mockUseSpmActions.mockReturnValue({
      createResponseActionPreview: {
        isPending: false,
        mutateAsync: mockCreateResponseActionPreview,
      },
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
    mockUseSpmInventoryTaxonomy.mockReturnValue({
      data: {
        harnesses: {
          claude_code: {
            bindings: [],
            item_types: [
              {
                description: "",
                display_value: "mcp_server",
                icon_key: "server",
                key: "mcp_server",
              },
              {
                description: "",
                display_value: "instruction_file",
                icon_key: "file_text",
                key: "instruction_file",
              },
            ],
            relationship_types: [],
            source_types: [
              {
                description: "",
                display_value: "claude_json",
                icon_key: "file_json",
                key: "claude_json",
              },
              {
                description: "",
                display_value: "claude_md",
                icon_key: "file_text",
                key: "claude_md",
              },
              {
                description: "",
                display_value: "agents_md",
                icon_key: "file_text",
                key: "agents_md",
              },
            ],
          },
        },
        version: 1,
      },
      isLoading: false,
    })
    mockDeleteEndpoint.mockResolvedValue(undefined)
    mockUseSpmEndpoints.mockReturnValue(paginated(endpoints))
    mockUseSpmInventory.mockImplementation(
      (params?: { endpointId?: string }) => {
        if (params?.endpointId) {
          const filteredInventory =
            params.endpointId === "endpoint-2"
              ? inventoryItems.slice(1)
              : [inventoryItems[0]]
          return paginated(filteredInventory)
        }
        return paginated(inventoryItems)
      }
    )
    mockUseSpmEndpointInventoryForEndpoints.mockImplementation(
      (endpointIds: string[]) =>
        endpointIds.map((endpointId) =>
          paginated(
            inventoryItems
              .filter((item) => item.identity_key.startsWith(`${endpointId}:`))
              .map((item) => endpointInventoryItem(item, endpointId))
          )
        )
    )
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
          "instruction_file"
        ),
      ],
      isLoading: false,
    })
    mockUseSpmResponseActions.mockReturnValue({
      data: [
        responseAction("disable_mcp_server", "Disable MCP server", [
          "mcp_server",
        ]),
        responseAction("exclude_instruction_file", "Exclude instruction file", [
          "instruction_file",
        ]),
      ],
      isLoading: false,
    })
    mockUseSpmResponseActionPreview.mockReturnValue({
      data: undefined,
      isLoading: false,
    })
    mockCreateResponseActionPreview.mockResolvedValue({
      action: "disable_mcp_server",
      after_content: '{\n  "mcpServers": {}\n}\n',
      before_content: "{}\n",
      completed_at: null,
      created_at: "2026-04-22T00:00:00Z",
      endpoint_id: "endpoint-1",
      error: null,
      expires_at: "2026-04-22T00:15:00Z",
      finding_id: "finding-1",
      id: "preview-1",
      organization_id: "org-1",
      payload: {},
      requested_by_user_id: null,
      result: {},
      status: "pending",
      target_path: null,
      updated_at: "2026-04-22T00:00:00Z",
    })
    mockUseSpmFindings.mockImplementation(
      (params?: { controlId?: string; endpointId?: string }) => {
        const findings = [
          finding(
            "finding-1",
            "endpoint-1",
            "item-1",
            "mcp_server",
            "claude_json",
            {
              recommended_action: "disable_mcp_server",
              summary: "Github MCP server is not approved",
            }
          ),
          finding(
            "finding-2",
            "endpoint-2",
            "item-2",
            "instruction_file",
            "claude_md",
            {
              summary: "CLAUDE.md should be excluded",
            }
          ),
          finding(
            "finding-3",
            "endpoint-2",
            "item-3",
            "instruction_file",
            "agents_md",
            {
              summary: "AGENTS.md is inventory only",
            }
          ),
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

  it("groups inventory by endpoint using endpoint-scoped inventory", async () => {
    render(<SpmInventoryView />)

    await waitFor(() => {
      expect(mockUseSpmEndpointInventoryForEndpoints).toHaveBeenLastCalledWith([
        "endpoint-1",
        "endpoint-2",
      ])
    })
    expect(screen.getAllByText("CLAUDE.md").length).toBeGreaterThan(0)
    expect(screen.getByText("/Users/chris/.claude.json")).toBeInTheDocument()
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

  it("filters findings by search and resets the feed filters", async () => {
    render(<SpmFindingsView />)

    fireEvent.change(screen.getByPlaceholderText("Search findings..."), {
      target: { value: "Github" },
    })

    await waitFor(() => {
      expect(
        screen.getByText("Github MCP server is not approved")
      ).toBeInTheDocument()
    })
    expect(screen.queryByText("CLAUDE.md should be excluded")).toBeNull()

    fireEvent.click(screen.getByRole("button", { name: /Reset/i }))

    await waitFor(() => {
      expect(
        screen.getByText("Github MCP server is not approved")
      ).toBeInTheDocument()
    })
  })

  it("opens a finding drawer and requests a response action preview", async () => {
    render(<SpmFindingsView />)

    fireEvent.click(
      screen.getByRole("button", {
        name: /Github MCP server is not approved/i,
      })
    )

    await waitFor(() => {
      expect(mockCreateResponseActionPreview).toHaveBeenCalledWith({
        findingId: "finding-1",
        requestBody: {},
      })
    })
    expect(screen.getByText("Disable MCP server")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Enforce" })).toBeInTheDocument()
  })

  it("renders the response actions catalog", () => {
    render(<SpmResponseActionsView />)

    expect(screen.getByText("Disable MCP server")).toBeInTheDocument()
    expect(screen.getByText("Exclude instruction file")).toBeInTheDocument()
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
    expect(screen.getByText("Status")).toBeInTheDocument()
    expect(screen.getByText("Compliance")).toBeInTheDocument()
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

    expect(screen.getByText("2 endpoints")).toBeInTheDocument()
    expect(screen.getByText("Chris MacBook")).toBeInTheDocument()
    expect(screen.getByText("CI Mac Mini")).toBeInTheDocument()
  })

  it("shows refreshing endpoint header status while keeping cached rows", () => {
    mockUseSpmEndpoints.mockReturnValue({
      ...paginated(endpoints),
      isFetching: true,
    })

    render(<SpmEndpointsView />)

    expect(screen.getByText("2 endpoints")).toBeInTheDocument()
    expect(screen.getByText("Chris MacBook")).toBeInTheDocument()
    expect(screen.getByText("CI Mac Mini")).toBeInTheDocument()
  })

  it("renders endpoint compliance from the endpoint payload while findings are loading", () => {
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
    expect(screen.getAllByText("compliant").length).toBeGreaterThan(0)
    expect(mockUseSpmFindings).not.toHaveBeenCalled()
  })

  it("does not block endpoints when findings fail to load", () => {
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
    expect(
      screen.queryByText(/Findings are unavailable/)
    ).not.toBeInTheDocument()
    expect(screen.queryByText(/database unavailable/)).not.toBeInTheDocument()
    expect(mockUseSpmFindings).not.toHaveBeenCalled()
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
