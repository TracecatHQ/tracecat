import { fireEvent, render, screen } from "@testing-library/react"
import type { ReactNode } from "react"
import { FormProvider, useForm } from "react-hook-form"
import type { ActionRead } from "@/client"
import { ActionNodeToolbar } from "@/components/builder/canvas/action-node"

const mockPush = jest.fn()

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
}))

jest.mock("next/link", () => ({
  __esModule: true,
  default: ({ children, ...props }: { children: ReactNode }) => (
    <a {...props}>{children}</a>
  ),
}))

jest.mock("@xyflow/react", () => ({
  ...jest.requireActual("@xyflow/react"),
  NodeToolbar: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}))

jest.mock("@/providers/builder", () => ({
  useWorkflowBuilder: () => ({
    workspaceId: "ws-1",
    setRerunRequest: jest.fn(),
  }),
}))

jest.mock("@/providers/workflow", () => ({ useWorkflow: jest.fn() }))
jest.mock("@/lib/hooks", () => ({}))
jest.mock("@/lib/query", () => ({ useQueryClient: jest.fn() }))

type ToolbarProps = Parameters<typeof ActionNodeToolbar>[0]

const action = {
  id: "act-1",
  title: "Execute child",
  type: "core.workflow.execute",
} as ActionRead

function renderToolbar(overrides: Partial<ToolbarProps> = {}) {
  function Harness() {
    const form = useForm()
    return (
      <FormProvider {...form}>
        <ActionNodeToolbar
          action={action}
          childWorkflowInfo={{
            isChildWorkflow: true,
            childWorkflowAlias: "child_flow",
            childIdFromAlias: "wf_child",
            isDynamicChildWorkflowAlias: false,
          }}
          tableLinkInfo={null}
          showToolbar
          selected
          sidebarRef={{ current: null }}
          setSelectedActionEventRef={jest.fn()}
          setIsMouseOverToolbar={jest.fn()}
          handleDeleteNode={jest.fn()}
          handleDuplicateNode={jest.fn()}
          locked={false}
          onToggleLock={jest.fn()}
          {...overrides}
        />
      </FormProvider>
    )
  }
  return render(<Harness />)
}

describe("ActionNodeToolbar", () => {
  beforeAll(() => {
    global.ResizeObserver = class ResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    Element.prototype.scrollIntoView = jest.fn()
  })

  beforeEach(() => {
    mockPush.mockClear()
  })

  it("does not cap the menu height", () => {
    // The menu outgrew the default 300px CommandList cap, which hid
    // "Open subflow" behind a hidden scrollbar.
    renderToolbar()
    const list = screen.getByRole("listbox")
    expect(list.className).toContain("max-h-[none]")
    expect(list.className).not.toMatch(/max-h-\[\d+px\]/)
  })

  it("opens the subflow from anywhere on the row", () => {
    renderToolbar()
    const item = screen.getByRole("option", { name: "Open subflow" })
    expect(item).toHaveAttribute("aria-disabled", "false")
    expect(item.querySelector("a")).toHaveAttribute(
      "href",
      "/workspaces/ws-1/workflows/wf_child"
    )

    fireEvent.click(item)
    expect(mockPush).toHaveBeenCalledWith("/workspaces/ws-1/workflows/wf_child")
  })

  it("does not navigate twice when the link itself is clicked", () => {
    renderToolbar()
    fireEvent.click(screen.getByText("Open subflow"))
    expect(mockPush).not.toHaveBeenCalled()
  })

  it("links by workflow id when no alias is set", () => {
    renderToolbar({
      childWorkflowInfo: {
        isChildWorkflow: true,
        childWorkflowId: "wf_by_id",
        isDynamicChildWorkflowAlias: false,
      },
    })
    fireEvent.click(screen.getByRole("option", { name: "Open subflow" }))
    expect(mockPush).toHaveBeenCalledWith("/workspaces/ws-1/workflows/wf_by_id")
  })

  it("disables the item when the alias does not resolve", () => {
    renderToolbar({
      childWorkflowInfo: {
        isChildWorkflow: true,
        childWorkflowAlias: "missing_flow",
        isDynamicChildWorkflowAlias: false,
      },
    })
    const item = screen.getByRole("option", { name: "Open subflow" })
    expect(item).toHaveAttribute("aria-disabled", "true")
    expect(item.querySelector("a")).toBeNull()
    fireEvent.click(item)
    expect(mockPush).not.toHaveBeenCalled()
  })

  it("opens the linked table from the row", () => {
    renderToolbar({
      childWorkflowInfo: {
        isChildWorkflow: false,
        isDynamicChildWorkflowAlias: false,
      },
      tableLinkInfo: {
        inputKey: "table",
        by: "name",
        value: "alerts",
        tableId: "tbl-1",
        tableName: "alerts",
        isDynamic: false,
        isLoading: false,
      },
    })
    expect(screen.queryByText("Open subflow")).toBeNull()
    fireEvent.click(screen.getByRole("option", { name: "Open table" }))
    expect(mockPush).toHaveBeenCalledWith("/workspaces/ws-1/tables/tbl-1")
  })
})
