import { fireEvent, render, screen } from "@testing-library/react"
import type { RegistryActionReadMinimal } from "@/client"
import { CanvasToolbar } from "@/components/builder/canvas/canvas-toolbar"
import { useBuilderRegistryActions } from "@/lib/hooks"

jest.mock("@/lib/hooks", () => ({
  useBuilderRegistryActions: jest.fn(),
}))

const mockUseBuilderRegistryActions =
  useBuilderRegistryActions as jest.MockedFunction<
    typeof useBuilderRegistryActions
  >

function createRegistryAction(
  overrides: Partial<RegistryActionReadMinimal> &
    Pick<RegistryActionReadMinimal, "action" | "namespace" | "description">
): RegistryActionReadMinimal {
  return {
    id: overrides.action,
    name: overrides.action,
    type: "template",
    origin: "tracecat",
    ...overrides,
  }
}

const registryActions: RegistryActionReadMinimal[] = [
  createRegistryAction({
    action: "core.http_request",
    default_title: "HTTP request",
    description: "",
    display_group: "HTTP",
    namespace: "core",
  }),
  createRegistryAction({
    action: "core.loop.start",
    default_title: "Loop start",
    description: "",
    display_group: "Data Transform",
    namespace: "core.loop",
  }),
  createRegistryAction({
    action: "core.loop.end",
    default_title: "Loop end",
    description: "",
    display_group: "Data Transform",
    namespace: "core.loop",
  }),
  createRegistryAction({
    action: "core.ssh.execute_command",
    default_title: "Execute SSH command",
    description: "",
    display_group: "SSH",
    namespace: "core.ssh",
  }),
  createRegistryAction({
    action: "tools.ansible.run_playbook",
    default_title: "Run Ansible playbook",
    description: "",
    display_group: "Ansible",
    namespace: "tools.ansible",
  }),
]

describe("CanvasToolbar", () => {
  beforeEach(() => {
    global.ResizeObserver = class ResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    }

    mockUseBuilderRegistryActions.mockReturnValue({
      registryActions,
      registryActionsIsLoading: false,
      registryActionsError: null,
      getRegistryAction: () => undefined,
    })
  })

  afterEach(() => {
    jest.clearAllMocks()
  })

  it("shows SSH actions in Core and loop actions in Workflow", () => {
    render(<CanvasToolbar onAddAction={jest.fn()} />)

    const toolbarButtons = screen.getAllByRole("button")

    fireEvent.click(toolbarButtons[0])
    expect(screen.queryByText("Loop start")).not.toBeInTheDocument()
    expect(screen.queryByText("Loop end")).not.toBeInTheDocument()
    expect(screen.getByText("Execute SSH command")).toBeInTheDocument()

    fireEvent.click(toolbarButtons[3])
    expect(screen.getByText("Loop start")).toBeInTheDocument()
    expect(screen.getByText("Loop end")).toBeInTheDocument()
  })

  it("shows Ansible actions in the Tools dropdown", () => {
    render(<CanvasToolbar onAddAction={jest.fn()} />)

    const toolbarButtons = screen.getAllByRole("button")
    fireEvent.click(toolbarButtons[toolbarButtons.length - 1])

    expect(screen.getByText("Run Ansible playbook")).toBeInTheDocument()
    expect(screen.getByText("tools.ansible.run_playbook")).toBeInTheDocument()
  })
})
