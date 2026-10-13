import { renderHook, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import { usersGetMyScopes, type WorkspaceReadMinimal } from "@/client"
import { useLandingWorkspace } from "@/hooks/use-landing-workspace"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client", () => ({ usersGetMyScopes: jest.fn() }))

const getMyScopes = jest.mocked(usersGetMyScopes)

const INTAKE = ["form:read", "form:submit"]
const BUILDER = ["workspace:read", "workflow:read", "form:read"]

const WORKSPACES = [
  { id: "workspace-helpdesk", name: "Helpdesk" },
  { id: "workspace-security", name: "Security" },
  { id: "workspace-platform", name: "Platform" },
] as WorkspaceReadMinimal[]

let scopesByWorkspace: Record<string, string[] | Error>

function renderLanding(
  props: Partial<Parameters<typeof useLandingWorkspace>[0]> = {}
) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  function wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
  }
  return renderHook(
    () =>
      useLandingWorkspace({
        workspaces: WORKSPACES,
        lastViewedWorkspaceId: undefined,
        enabled: true,
        ...props,
      }),
    { wrapper }
  )
}

beforeEach(() => {
  jest.clearAllMocks()
  scopesByWorkspace = {
    "workspace-helpdesk": BUILDER,
    "workspace-security": BUILDER,
    "workspace-platform": BUILDER,
  }
  getMyScopes.mockImplementation((({ workspaceId = "" } = {}) => {
    const scopes = scopesByWorkspace[workspaceId]
    return scopes instanceof Error
      ? Promise.reject(scopes)
      : Promise.resolve({ scopes })
  }) as unknown as typeof usersGetMyScopes)
})

describe("useLandingWorkspace", () => {
  it("uses the last-viewed workspace without asking for scopes", () => {
    const { result } = renderLanding({
      lastViewedWorkspaceId: "workspace-platform",
    })

    expect(result.current.workspaceId).toBe("workspace-platform")
    expect(getMyScopes).not.toHaveBeenCalled()
  })

  it("asks nothing when the URL already names a workspace", () => {
    const { result } = renderLanding({ enabled: false })

    expect(result.current.workspaceId).toBe("workspace-helpdesk")
    expect(getMyScopes).not.toHaveBeenCalled()
  })

  it("asks nothing for a user with one workspace", () => {
    const { result } = renderLanding({ workspaces: [WORKSPACES[0]] })

    expect(result.current.workspaceId).toBe("workspace-helpdesk")
    expect(getMyScopes).not.toHaveBeenCalled()
  })

  it("stops at the first workspace when the user can read it", async () => {
    const { result } = renderLanding()
    // Nothing is chosen, so nothing can redirect, while scopes load.
    expect(result.current.workspaceId).toBeUndefined()

    await waitFor(() =>
      expect(result.current.workspaceId).toBe("workspace-helpdesk")
    )
    expect(getMyScopes).toHaveBeenCalledTimes(1)
  })

  it("skips a workspace where the user is intake-only", async () => {
    scopesByWorkspace["workspace-helpdesk"] = INTAKE

    const { result } = renderLanding()

    await waitFor(() =>
      expect(result.current.workspaceId).toBe("workspace-security")
    )
    // One at a time, and not past the first readable workspace.
    expect(getMyScopes.mock.calls.map(([data]) => data?.workspaceId)).toEqual([
      "workspace-helpdesk",
      "workspace-security",
    ])
  })

  it("moves on when a workspace's scopes request fails", async () => {
    scopesByWorkspace["workspace-helpdesk"] = new Error("network down")
    scopesByWorkspace["workspace-security"] = INTAKE

    const { result } = renderLanding()

    await waitFor(() =>
      expect(result.current.workspaceId).toBe("workspace-platform")
    )
  })

  it("falls back to the first workspace when none is readable", async () => {
    scopesByWorkspace = {
      "workspace-helpdesk": INTAKE,
      "workspace-security": INTAKE,
      "workspace-platform": new Error("network down"),
    }

    const { result } = renderLanding()

    await waitFor(() =>
      expect(result.current.workspaceId).toBe("workspace-helpdesk")
    )
    expect(getMyScopes).toHaveBeenCalledTimes(3)
  })
})
