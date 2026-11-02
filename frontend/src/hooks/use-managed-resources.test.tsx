import { renderHook, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import {
  type ManagedResourceRead,
  managedResourcesListDetachedCopies,
  managedResourcesListManagedResources,
} from "@/client"
import { useManagedResources } from "@/hooks/use-managed-resources"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client", () => ({
  managedResourcesListManagedResources: jest.fn(),
  managedResourcesListDetachedCopies: jest.fn(),
  managedResourcesDetachManagedResource: jest.fn(),
}))

const listManaged = jest.mocked(managedResourcesListManagedResources)
const listDetached = jest.mocked(managedResourcesListDetachedCopies)

function managed(
  resourceType: ManagedResourceRead["resource_type"],
  localId: string,
  sourceId: string
): ManagedResourceRead {
  return {
    resource_type: resourceType,
    local_id: localId,
    source_id: sourceId,
    deployed_tag: "v1.0.0",
    deployed_commit_sha: "abc123",
    deployed_at: "2026-01-01T00:00:00Z",
  }
}

function setup() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
  return renderHook(() => useManagedResources("workspace-1"), { wrapper })
}

beforeEach(() => {
  jest.clearAllMocks()
  listDetached.mockResolvedValue({
    items: [
      {
        resource_type: "workflow",
        local_id: "wf-copy",
        original_local_id: "wf-1",
        original_name: "triage",
        detached_at: "2026-01-02T00:00:00Z",
      },
      {
        resource_type: "case_field",
        local_id: "field-copy-uuid",
        original_local_id: "derived-uuid",
        original_name: "severity_score",
        detached_at: "2026-01-02T00:00:00Z",
        case_field_name: "severity_copy",
      },
    ],
    next_cursor: null,
  })
})

describe("useManagedResources", () => {
  it("loads every page and looks resources up by type and id", async () => {
    listManaged
      .mockResolvedValueOnce({
        items: [
          {
            ...managed("workflow", "wf-1", "src-wf-1"),
            workflow_id: "wf_short1",
          },
        ],
        next_cursor: "page-2",
      })
      .mockResolvedValueOnce({
        items: [
          {
            ...managed("case_field", "derived-uuid", "severity_src"),
            case_field_name: "severity_score",
          },
        ],
        next_cursor: null,
      })

    const { result } = setup()

    await waitFor(() => expect(result.current.managedResources).toHaveLength(2))
    expect(listManaged).toHaveBeenNthCalledWith(2, {
      workspaceId: "workspace-1",
      limit: 200,
      cursor: "page-2",
    })
    expect(result.current.isManaged("workflow", "wf-1")).toBe(true)
    // Workflow APIs return the short id; it finds the same record.
    expect(result.current.isManaged("workflow", "wf_short1")).toBe(true)
    // Same id, other type: not managed.
    expect(result.current.isManaged("table", "wf-1")).toBe(false)
    // Case fields are keyed by their current name, not their derived local
    // id or their package source id.
    expect(result.current.isManaged("case_field", "severity_score")).toBe(true)
    expect(result.current.isManaged("case_field", "derived-uuid")).toBe(false)
    expect(result.current.isManaged("case_field", "severity_src")).toBe(false)
    expect(result.current.getManaged("workflow", "wf-1")?.deployed_tag).toBe(
      "v1.0.0"
    )
    await waitFor(() =>
      expect(
        result.current.getDetachedCopy("workflow", "wf-copy")?.original_name
      ).toBe("triage")
    )
    expect(
      result.current.getDetachedCopy("case_field", "severity_copy")
        ?.original_name
    ).toBe("severity_score")
  })

  it("loads every page of detached copies", async () => {
    listManaged.mockResolvedValue({ items: [], next_cursor: null })
    listDetached.mockReset()
    listDetached
      .mockResolvedValueOnce({
        items: [
          {
            resource_type: "workflow",
            local_id: "wf-copy",
            original_local_id: "wf-1",
            original_name: "triage",
            detached_at: "2026-01-02T00:00:00Z",
          },
        ],
        next_cursor: "page-2",
      })
      .mockResolvedValueOnce({
        items: [
          {
            resource_type: "table",
            local_id: "table-copy",
            original_local_id: "table-1",
            original_name: "assets",
            detached_at: "2026-01-03T00:00:00Z",
          },
        ],
        next_cursor: null,
      })

    const { result } = setup()

    await waitFor(() => expect(result.current.detachedCopies).toHaveLength(2))
    expect(listDetached).toHaveBeenNthCalledWith(2, {
      workspaceId: "workspace-1",
      limit: 200,
      cursor: "page-2",
    })
    expect(result.current.getDetachedCopy("workflow", "wf-copy")).toBeDefined()
    expect(
      result.current.getDetachedCopy("table", "table-copy")?.original_name
    ).toBe("assets")
  })

  it("treats everything as editable when the list fails", async () => {
    listManaged.mockRejectedValueOnce(new Error("boom"))

    const { result } = setup()

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.isManaged("workflow", "wf-1")).toBe(false)
  })
})
