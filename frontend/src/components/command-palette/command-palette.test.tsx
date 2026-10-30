import { fireEvent, render, screen } from "@testing-library/react"
import { CommandPalette } from "@/components/command-palette/command-palette"

const push = jest.fn()
const setSettingsOpen = jest.fn()
let scopes = new Set<string>()
let workspaceId: string | undefined = "workspace-1"

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
}))
jest.mock("@/providers/scopes", () => ({
  useScopes: () => ({
    isLoading: false,
    hasScope: (scope: string) => scopes.has(scope),
    hasAnyScope: (required: string[]) => required.some((s) => scopes.has(s)),
  }),
}))
jest.mock("@/providers/workspace-id", () => ({
  useOptionalWorkspaceId: () => workspaceId,
}))
jest.mock("@/hooks/use-entitlements", () => ({
  useEntitlements: () => ({ hasEntitlement: () => true }),
}))
jest.mock("@/hooks/use-forms-access", () => ({
  useFormsAccess: () => "hidden",
}))
jest.mock("@/components/settings/settings-modal-context", () => ({
  useSettingsModal: () => ({ setOpen: setSettingsOpen }),
}))
jest.mock("@/lib/hooks", () => ({
  useWorkspaceManager: () => ({
    workspaces: [
      { id: "workspace-1", name: "Security ops" },
      { id: "workspace-2", name: "Detection eng" },
    ],
  }),
  useWorkflowManager: () => ({
    workflows: [{ id: "wf_abc", title: "Triage phishing", alias: "phish" }],
  }),
}))

function openPalette() {
  fireEvent.keyDown(window, { key: "k", metaKey: true })
}

describe("CommandPalette", () => {
  beforeEach(() => {
    push.mockClear()
    setSettingsOpen.mockClear()
    scopes = new Set(["workflow:read", "case:read"])
    workspaceId = "workspace-1"
  })

  it("opens on Cmd+K and lists only pages the user can access", () => {
    render(<CommandPalette />)
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()

    openPalette()

    expect(screen.getByRole("dialog")).toBeInTheDocument()
    expect(screen.getByRole("option", { name: "Cases" })).toBeInTheDocument()
    expect(screen.getByRole("option", { name: "Runs" })).toBeInTheDocument()
    expect(
      screen.queryByRole("option", { name: "Tables" })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole("option", { name: "Audit logs" })
    ).not.toBeInTheDocument()
  })

  it("closes on Cmd+K pressed in the search input", () => {
    render(<CommandPalette />)
    openPalette()

    fireEvent.keyDown(screen.getByRole("combobox"), {
      key: "k",
      ctrlKey: true,
    })

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  })

  it("navigates to a workflow and closes", () => {
    render(<CommandPalette />)
    openPalette()

    fireEvent.click(screen.getByRole("option", { name: /Triage phishing/ }))

    expect(push).toHaveBeenCalledWith(
      "/workspaces/workspace-1/workflows/wf_abc"
    )
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  })

  it("switches workspaces", () => {
    render(<CommandPalette />)
    openPalette()

    fireEvent.click(screen.getByRole("option", { name: /Detection eng/ }))

    expect(push).toHaveBeenCalledWith("/workspaces/workspace-2")
  })

  it("stays shut when another handler already claimed Cmd+K", () => {
    const claim = (event: KeyboardEvent) => event.preventDefault()
    window.addEventListener("keydown", claim, true)
    render(<CommandPalette />)

    openPalette()

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
    window.removeEventListener("keydown", claim, true)
  })

  it("shows organization pages without workspace pages outside a workspace", () => {
    workspaceId = undefined
    scopes = new Set(["org:settings:read", "workflow:read"])
    render(<CommandPalette />)
    openPalette()

    expect(
      screen.getByRole("option", { name: "Audit logs" })
    ).toBeInTheDocument()
    expect(
      screen.queryByRole("option", { name: "Runs" })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole("option", { name: /Triage phishing/ })
    ).not.toBeInTheDocument()
  })
})
