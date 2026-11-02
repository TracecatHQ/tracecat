import { act, renderHook, waitFor } from "@testing-library/react"
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

function askedFor(): (string | null | undefined)[] {
  return jest
    .mocked(usersGetMyScopes)
    .mock.calls.map(([args]) => args?.workspaceId)
}

beforeEach(() => {
  jest.mocked(usersGetMyScopes).mockClear()
})

it.each([
  // The last-viewed workspace is checked first, then the rest in order.
  [{ a: ["workspace:read"], b: ["workspace:read"] }, "b", ["b"]],
  // Its access can have changed since: intake-only now, so it is skipped.
  [{ a: ["workspace:read"], b: INTAKE }, "a", ["b", "a"]],
  // Nothing is readable: keep the last-viewed one.
  [{ a: INTAKE, b: INTAKE }, "b", ["b", "a"]],
])(
  "lands a user with %p, who last viewed b, in %s",
  async (scopes, landing, asked) => {
    const result = renderLanding(scopes, "b")

    expect(result.current).toBeUndefined()
    await waitFor(() => expect(result.current).toBe(landing))
    expect(askedFor()).toEqual(asked)
  }
)

it("starts the scan over when the workspace list changes", async () => {
  // c answers only when told to, so the list changes while c is asked for.
  let answerC: (value: { scopes: string[] }) => void = () => {}
  jest.mocked(usersGetMyScopes).mockImplementation((({
    workspaceId,
  }: {
    workspaceId?: string
  }) =>
    workspaceId === "c"
      ? new Promise((resolve) => {
          answerC = resolve
        })
      : Promise.resolve({ scopes: INTAKE })) as never)
  const client = new QueryClient({ defaultOptions: { queries: { retry: 0 } } })
  const { result, rerender } = renderHook(
    ({ workspaces }: { workspaces: WorkspaceReadMinimal[] }) =>
      useLandingWorkspace({
        workspaces,
        lastViewedWorkspaceId: undefined,
        enabled: true,
      }),
    {
      initialProps: {
        workspaces: [{ id: "a" }, { id: "b" }, { id: "c" }] as never,
      },
      wrapper: ({ children }: { children: ReactNode }) => (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      ),
    }
  )
  await waitFor(() => expect(askedFor()).toContain("c"))

  rerender({ workspaces: [{ id: "a" }, { id: "c" }] as never })
  act(() => answerC({ scopes: ["workspace:read"] }))

  await waitFor(() => expect(result.current).toBe("c"))
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
