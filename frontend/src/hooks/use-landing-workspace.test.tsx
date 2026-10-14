import { renderHook, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import { usersGetMyScopes, type WorkspaceReadMinimal } from "@/client"
import { useLandingWorkspace } from "@/hooks/use-landing-workspace"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client", () => ({ usersGetMyScopes: jest.fn() }))

const INTAKE = ["form:read", "form:submit"]

function renderLanding(
  scopes: Record<string, string[] | Error>,
  lastViewedWorkspaceId?: string
) {
  jest
    .mocked(usersGetMyScopes)
    .mockImplementation((({ workspaceId = "" }) =>
      scopes[workspaceId] instanceof Error
        ? Promise.reject(scopes[workspaceId])
        : Promise.resolve({ scopes: scopes[workspaceId] })) as never)
  const workspaces = Object.keys(scopes).map((id) => ({ id }))
  const client = new QueryClient({ defaultOptions: { queries: { retry: 0 } } })
  return renderHook(
    () =>
      useLandingWorkspace({
        workspaces: workspaces as WorkspaceReadMinimal[],
        lastViewedWorkspaceId,
        enabled: true,
      }),
    {
      wrapper: ({ children }: { children: ReactNode }) => (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      ),
    }
  ).result
}

it("uses the last-viewed workspace without asking for scopes", () => {
  expect(renderLanding({ a: INTAKE, b: INTAKE }, "b").current).toBe("b")
  expect(usersGetMyScopes).not.toHaveBeenCalled()
})

it.each([
  // Intake-only or unknown workspaces are skipped; nothing is chosen
  // (so nothing redirects) while scopes load.
  [{ a: INTAKE, b: new Error("down"), c: ["workspace:read"] }, "c"],
  [{ a: INTAKE, b: INTAKE }, "a"],
])("lands a user with %p in %s", async (scopes, landing) => {
  const result = renderLanding(scopes)

  expect(result.current).toBeUndefined()
  await waitFor(() => expect(result.current).toBe(landing))
})
