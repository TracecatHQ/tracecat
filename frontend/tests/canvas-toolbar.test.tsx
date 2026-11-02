import { fireEvent, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { RegistryActionReadMinimal } from "@/client"
import { CanvasToolbar } from "@/components/builder/canvas/canvas-toolbar"
import { useBuilderRegistryActions } from "@/lib/hooks"

jest.mock("@/lib/hooks", () => ({
  useBuilderRegistryActions: jest.fn(),
}))

// jsdom has no layout; give the virtualized list a viewport to render into.
let originalHeight: PropertyDescriptor | undefined
let originalWidth: PropertyDescriptor | undefined
let originalScrollTo: typeof HTMLElement.prototype.scrollTo
beforeAll(() => {
  originalWidth = Object.getOwnPropertyDescriptor(
    HTMLElement.prototype,
    "offsetWidth"
  )
  originalScrollTo = HTMLElement.prototype.scrollTo
  Object.defineProperty(HTMLElement.prototype, "offsetWidth", {
    configurable: true,
    get: () => 1000,
  })
  HTMLElement.prototype.scrollTo = () => {}
  originalHeight = Object.getOwnPropertyDescriptor(
    HTMLElement.prototype,
    "offsetHeight"
  )
  Object.defineProperty(HTMLElement.prototype, "offsetHeight", {
    configurable: true,
    get: () => 300,
  })
})
afterAll(() => {
  if (originalHeight)
    Object.defineProperty(HTMLElement.prototype, "offsetHeight", originalHeight)
  if (originalWidth)
    Object.defineProperty(HTMLElement.prototype, "offsetWidth", originalWidth)
  HTMLElement.prototype.scrollTo = originalScrollTo
})

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

  it("adds the actions picked in the Tools dialog on Done", async () => {
    const user = userEvent.setup()
    const onAddActions = jest.fn()
    render(
      <CanvasToolbar onAddAction={jest.fn()} onAddActions={onAddActions} />
    )

    await user.click(screen.getByRole("button", { name: "Tools" }))
    expect(screen.getByRole("dialog")).toBeInTheDocument()
    expect(
      screen.queryByRole("option", { name: /HTTP request/ })
    ).not.toBeInTheDocument()

    await user.click(
      await screen.findByRole("option", { name: /Run Ansible playbook/ })
    )
    await user.click(screen.getByRole("button", { name: "Done" }))

    expect(onAddActions).toHaveBeenCalledWith([
      expect.objectContaining({ action: "tools.ansible.run_playbook" }),
    ])
  })

  it("falls back to onAddAction per picked tool", async () => {
    const user = userEvent.setup()
    const onAddAction = jest.fn()
    render(<CanvasToolbar onAddAction={onAddAction} />)

    await user.click(screen.getByRole("button", { name: "Tools" }))
    await user.click(
      await screen.findByRole("option", { name: /Run Ansible playbook/ })
    )
    await user.click(screen.getByRole("button", { name: "Done" }))

    expect(onAddAction).toHaveBeenCalledTimes(1)
    expect(onAddAction).toHaveBeenCalledWith(
      expect.objectContaining({ action: "tools.ansible.run_playbook" })
    )
  })
})
