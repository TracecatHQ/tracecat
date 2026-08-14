/**
 * @jest-environment jsdom
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { CaseRead, CaseTaskRead, WorkflowReadMinimal } from "@/client"
import { CaseTasksPanel } from "@/components/cases/case-tasks-panel"
import { useWorkspaceMembers } from "@/hooks/use-workspace"
import {
  useCaseTasks,
  useCreateCaseTask,
  useDeleteCaseTask,
  useUpdateCaseTask,
  useWorkflowManager,
} from "@/lib/hooks"

jest.mock("@/lib/hooks", () => ({
  useCaseTasks: jest.fn(),
  useCreateCaseTask: jest.fn(),
  useUpdateCaseTask: jest.fn(),
  useDeleteCaseTask: jest.fn(),
  useWorkflowManager: jest.fn(),
}))

jest.mock("@/hooks/use-workspace", () => ({
  useWorkspaceMembers: jest.fn(),
}))

// The run pill's dialog: a marker that records whether it is open.
jest.mock("@/components/cases/workflow-trigger-dialog", () => {
  const React = require("react")
  return {
    WorkflowTriggerDialog: ({ open }: { open: boolean }) =>
      React.createElement("div", {
        "data-testid": "workflow-trigger-dialog",
        "data-open": String(open),
      }),
  }
})

// The read row renders descriptions through the tiptap-backed comment
// viewer, which jsdom cannot host; the panel tests assert wiring, not
// markdown rendering, so a passthrough div stands in.
jest.mock("@/components/cases/case-description-editor", () => {
  const React = require("react")
  return {
    CaseCommentViewer: ({ content }: { content: string }) =>
      React.createElement(
        "div",
        { "data-testid": "task-description-viewer" },
        content
      ),
  }
})

const mockUseCaseTasks = useCaseTasks as jest.MockedFunction<
  typeof useCaseTasks
>
const mockUseCreateCaseTask = useCreateCaseTask as jest.MockedFunction<
  typeof useCreateCaseTask
>
const mockUseUpdateCaseTask = useUpdateCaseTask as jest.MockedFunction<
  typeof useUpdateCaseTask
>
const mockUseDeleteCaseTask = useDeleteCaseTask as jest.MockedFunction<
  typeof useDeleteCaseTask
>
const mockUseWorkflowManager = useWorkflowManager as jest.MockedFunction<
  typeof useWorkflowManager
>
const mockUseWorkspaceMembers = useWorkspaceMembers as jest.MockedFunction<
  typeof useWorkspaceMembers
>

class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
}

global.ResizeObserver = ResizeObserverMock as typeof ResizeObserver
// cmdk scrolls the selected option into view; Radix menus capture pointers.
// jsdom implements neither.
window.HTMLElement.prototype.scrollIntoView = jest.fn()
window.HTMLElement.prototype.hasPointerCapture = jest.fn()
window.HTMLElement.prototype.releasePointerCapture = jest.fn()

const caseData = { id: "case-1" } as unknown as CaseRead

function makeTask(overrides: Partial<CaseTaskRead> = {}): CaseTaskRead {
  return {
    id: "task-1",
    created_at: "2024-01-01T00:00:00Z",
    updated_at: "2024-01-01T00:00:00Z",
    case_id: "case-1",
    title: "Task one",
    description: null,
    priority: "unknown",
    status: "todo",
    assignee: null,
    workflow_id: null,
    ...overrides,
  }
}

function setTasks(tasks: CaseTaskRead[]) {
  mockUseCaseTasks.mockReturnValue({
    caseTasks: tasks,
    caseTasksIsLoading: false,
    caseTasksError: null,
    refetchCaseTasks: jest.fn(),
  } as unknown as ReturnType<typeof useCaseTasks>)
}

function renderPanel(outerKeyDown?: jest.Mock) {
  return render(
    <div onKeyDown={outerKeyDown}>
      <CaseTasksPanel caseId="case-1" workspaceId="ws-1" caseData={caseData} />
    </div>
  )
}

/**
 * Opens a Radix dropdown from its trigger. `click` alone does not: the trigger
 * opens on `pointerdown`, which jsdom does not synthesize from a click, so the
 * keyboard path is the one that works in tests.
 */
function openMenu(trigger: HTMLElement) {
  fireEvent.keyDown(trigger, { key: "Enter" })
}

/** Opens a row's right-click menu → Edit task. The row carries no `⋯`. */
async function openEditMode(
  user: ReturnType<typeof userEvent.setup>,
  title = "Task one"
) {
  fireEvent.contextMenu(screen.getByText(title))
  await user.click(await screen.findByRole("menuitem", { name: /edit task/i }))
}

describe("CaseTasksPanel", () => {
  let createTask: jest.Mock
  let updateTask: jest.Mock
  let deleteTask: jest.Mock

  beforeEach(() => {
    jest.clearAllMocks()
    createTask = jest.fn((_payload, options) => options?.onSuccess?.())
    updateTask = jest.fn((_payload, options) => options?.onSuccess?.())
    deleteTask = jest.fn((_payload, options) => options?.onSuccess?.())
    setTasks([])
    mockUseCreateCaseTask.mockReturnValue({
      createTask,
      createTaskIsPending: false,
      createTaskError: null,
    } as unknown as ReturnType<typeof useCreateCaseTask>)
    mockUseUpdateCaseTask.mockReturnValue({
      updateTask,
      updateTaskIsPending: false,
      updateTaskError: null,
    } as unknown as ReturnType<typeof useUpdateCaseTask>)
    mockUseDeleteCaseTask.mockReturnValue({
      deleteTask,
      deleteTaskIsPending: false,
      deleteTaskError: null,
    } as unknown as ReturnType<typeof useDeleteCaseTask>)
    mockUseWorkflowManager.mockReturnValue({
      workflows: [
        { id: "wf-1", title: "Containment workflow" },
      ] as WorkflowReadMinimal[],
    } as unknown as ReturnType<typeof useWorkflowManager>)
    mockUseWorkspaceMembers.mockReturnValue({
      members: [
        {
          user_id: "user-1",
          email: "analyst@example.com",
          first_name: "Ana",
          last_name: "Lyst",
          role_name: "basic",
        },
      ],
    } as unknown as ReturnType<typeof useWorkspaceMembers>)
  })

  // --- Read mode ----------------------------------------------------------

  it("renders a row per task with an inert title", () => {
    setTasks([
      makeTask({ id: "t1", title: "Plain task" }),
      makeTask({ id: "t2", title: "Documented task", description: "Notes" }),
    ])

    renderPanel()

    expect(screen.getByText("Plain task")).toBeInTheDocument()
    expect(screen.getByText("Documented task")).toBeInTheDocument()
    // Titles are text, not buttons: clicking them must do nothing.
    expect(
      screen.queryByRole("button", { name: "Plain task" })
    ).not.toBeInTheDocument()
    fireEvent.click(screen.getByText("Plain task"))
    expect(screen.queryByLabelText("Task title")).not.toBeInTheDocument()
  })

  it("hides the description until the chevron expands it", () => {
    setTasks([
      makeTask({ id: "t1", title: "Documented task", description: "- a" }),
    ])

    renderPanel()

    // Collapsed at rest: the chevron is the only trace of the description.
    expect(
      screen.queryByTestId("task-description-viewer")
    ).not.toBeInTheDocument()
    const chevron = screen.getByRole("button", { name: "Show description" })
    expect(chevron).toHaveAttribute("aria-expanded", "false")

    fireEvent.click(chevron)
    expect(screen.getByTestId("task-description-viewer")).toHaveTextContent(
      "- a"
    )

    fireEvent.click(screen.getByRole("button", { name: "Hide description" }))
    expect(
      screen.queryByTestId("task-description-viewer")
    ).not.toBeInTheDocument()
  })

  it("renders neither a chevron nor a description region when the description is empty", () => {
    setTasks([makeTask({ id: "t1", title: "Plain task" })])

    renderPanel()

    expect(
      screen.queryByTestId("task-description-viewer")
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Show description" })
    ).not.toBeInTheDocument()
  })

  it("orders one dense line: status, title, then the priority/assignee/workflow cluster", () => {
    setTasks([
      makeTask({
        id: "t1",
        title: "Ordered task",
        workflow_id: "wf-1",
        assignee: {
          id: "user-1",
          email: "analyst@example.com",
          role: "basic",
          first_name: "Ana",
          last_name: "Lyst",
          settings: {},
        },
      }),
    ])

    renderPanel()

    // Status glyph leads the line, the title follows it, and the metadata
    // cluster trails right-aligned — Linear's sub-issue row.
    const sequence = [
      screen.getByRole("button", { name: /change status/i }),
      screen.getByText("Ordered task"),
      screen.getByRole("button", { name: /set priority/i }),
      screen.getByRole("button", { name: /assign to/i }),
      screen.getByRole("button", { name: /run workflow/i }),
    ]
    for (let i = 0; i < sequence.length - 1; i++) {
      // DOCUMENT_POSITION_FOLLOWING: the next element comes after this one.
      expect(
        sequence[i].compareDocumentPosition(sequence[i + 1]) &
          Node.DOCUMENT_POSITION_FOLLOWING
      ).toBeTruthy()
    }
  })

  it("shows the assignee as an avatar when set and a muted placeholder when not", () => {
    setTasks([
      makeTask({
        id: "t1",
        title: "Assigned task",
        assignee: {
          id: "user-1",
          email: "analyst@example.com",
          role: "basic",
          first_name: "Ana",
          last_name: "Lyst",
          settings: {},
        },
      }),
      makeTask({ id: "t2", title: "Unassigned task" }),
    ])

    renderPanel()

    // Assigned: the row carries the avatar only — no name text at this
    // density — so the email rides on the accessible name instead.
    const assigned = screen.getByRole("button", {
      name: /currently analyst@example\.com/i,
    })
    expect(assigned).toBeInTheDocument()
    expect(assigned).not.toHaveTextContent("analyst@example.com")
    // Unassigned: the slot stays occupied by a muted placeholder, so the
    // picker is still reachable and the cluster never reflows.
    expect(
      screen.getByRole("button", { name: "Assign to" })
    ).toBeInTheDocument()
  })

  it("never nests buttons, even with a run pill on the card", () => {
    setTasks([
      makeTask({
        id: "t1",
        description: "Has description",
        workflow_id: "wf-1",
        assignee: {
          id: "user-1",
          email: "analyst@example.com",
          role: "basic",
          first_name: "Ana",
          last_name: "Lyst",
          settings: {},
        },
      }),
    ])

    const { container } = renderPanel()

    expect(container.querySelectorAll("button button")).toHaveLength(0)
  })

  // --- Live pills in read mode -------------------------------------------

  it("changes status from the pill's dropdown with a status-only patch", async () => {
    setTasks([makeTask({ status: "todo" })])

    renderPanel()
    openMenu(screen.getByRole("button", { name: /change status/i }))
    fireEvent.click(await screen.findByRole("menuitem", { name: /completed/i }))

    expect(updateTask).toHaveBeenCalledTimes(1)
    const payload = updateTask.mock.calls[0][0]
    expect(payload).toEqual({ status: "completed" })
    // Single-field patch: default_trigger_values must never ride along, or
    // it erases trigger defaults written by WorkflowTriggerDialog.
    expect("default_trigger_values" in payload).toBe(false)
  })

  it("drops search and the checkbox from the closed-set status dropdown", async () => {
    setTasks([makeTask({ status: "todo" })])

    renderPanel()
    openMenu(screen.getByRole("button", { name: /change status/i }))

    // Four fixed options: a plain dropdown like the properties rail's, with
    // no search box to filter them and no checkbox to multi-select them.
    const items = await screen.findAllByRole("menuitem")
    expect(items).toHaveLength(4)
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument()
    expect(
      screen.queryByPlaceholderText(/change status/i)
    ).not.toBeInTheDocument()
    // The task's own status is the one row marked current.
    const current = items.filter((item) =>
      /, current/.test(item.textContent ?? "")
    )
    expect(current).toHaveLength(1)
    expect(current[0]).toHaveTextContent(/^To do/)
  })

  it("leaves digits to the palette search instead of treating them as shortcuts", async () => {
    setTasks([makeTask({ status: "todo" })])

    renderPanel()
    fireEvent.click(screen.getByRole("button", { name: /assign to/i }))
    const search = await screen.findByPlaceholderText("Assign to…")
    fireEvent.keyDown(search, { key: "3" })

    // Digit shortcuts were removed from the task palettes: a digit only ever
    // types into the search, never selects a row.
    expect(updateTask).not.toHaveBeenCalled()
  })

  it("changes priority from the pill with a priority-only patch", async () => {
    setTasks([makeTask({ priority: "unknown" })])

    renderPanel()
    openMenu(screen.getByRole("button", { name: /set priority/i }))
    fireEvent.click(await screen.findByRole("menuitem", { name: /^high/i }))

    expect(updateTask).toHaveBeenCalledTimes(1)
    expect(updateTask.mock.calls[0][0]).toEqual({ priority: "high" })
  })

  it("maps the No assignee palette row to a null patch", async () => {
    setTasks([
      makeTask({
        assignee: {
          id: "user-1",
          email: "analyst@example.com",
          role: "basic",
          first_name: "Ana",
          last_name: "Lyst",
          settings: {},
        },
      }),
    ])

    renderPanel()
    fireEvent.click(screen.getByRole("button", { name: /assign to/i }))
    fireEvent.click(await screen.findByRole("option", { name: /no assignee/i }))

    expect(updateTask).toHaveBeenCalledTimes(1)
    expect(updateTask.mock.calls[0][0]).toEqual({ assignee_id: null })
  })

  // --- Workflow pill: run in read mode ------------------------------------

  it("runs the linked workflow from the pill instead of a play button", () => {
    setTasks([makeTask({ workflow_id: "wf-1" })])

    renderPanel()

    const runPill = screen.getByRole("button", {
      name: /run workflow: containment workflow/i,
    })
    expect(screen.getByTestId("workflow-trigger-dialog")).toHaveAttribute(
      "data-open",
      "false"
    )
    fireEvent.click(runPill)
    expect(screen.getByTestId("workflow-trigger-dialog")).toHaveAttribute(
      "data-open",
      "true"
    )
  })

  it("hides the workflow pill entirely when no workflow is set", () => {
    setTasks([makeTask({ workflow_id: null })])

    renderPanel()

    expect(
      screen.queryByRole("button", { name: /run workflow/i })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByTestId("workflow-trigger-dialog")
    ).not.toBeInTheDocument()
  })

  // --- The ⋯ menu and edit mode -------------------------------------------

  it("enters edit mode through right-click → Edit task, seeded from the task", async () => {
    const user = userEvent.setup()
    setTasks([makeTask({ title: "Task one", description: "Existing notes" })])

    renderPanel()
    await openEditMode(user)

    expect(screen.getByLabelText("Task title")).toHaveValue("Task one")
    expect(screen.getByLabelText("Task description")).toHaveValue(
      "Existing notes"
    )
  })

  it("saves an edit with exactly the six form keys, then returns to read", async () => {
    const user = userEvent.setup()
    setTasks([makeTask({ title: "Task one" })])

    renderPanel()
    await openEditMode(user)

    const input = screen.getByLabelText("Task title")
    fireEvent.change(input, { target: { value: "Task renamed" } })
    fireEvent.click(screen.getByRole("button", { name: "Save task" }))

    await waitFor(() => expect(updateTask).toHaveBeenCalledTimes(1))
    const payload = updateTask.mock.calls[0][0]
    expect(payload).toEqual({
      title: "Task renamed",
      description: "",
      status: "todo",
      priority: "unknown",
      assignee_id: null,
      workflow_id: null,
    })
    // default_trigger_values must never ride along — sending it as null
    // erases trigger defaults written by WorkflowTriggerDialog.
    expect("default_trigger_values" in payload).toBe(false)

    // The successful save closes edit mode.
    await waitFor(() =>
      expect(screen.queryByLabelText("Task title")).not.toBeInTheDocument()
    )
  })

  it("saves an edit from the description with Cmd+Enter", async () => {
    const user = userEvent.setup()
    setTasks([makeTask({ title: "Task one", description: "Notes" })])

    renderPanel()
    await openEditMode(user)

    const textarea = screen.getByLabelText("Task description")
    fireEvent.change(textarea, { target: { value: "- a\n**bold**" } })
    fireEvent.keyDown(textarea, { key: "Enter", metaKey: true })

    await waitFor(() => expect(updateTask).toHaveBeenCalledTimes(1))
    expect(updateTask.mock.calls[0][0]).toMatchObject({
      description: "- a\n**bold**",
    })
  })

  it("keeps Enter as a newline in the description instead of saving", async () => {
    const user = userEvent.setup()
    setTasks([makeTask({ title: "Task one", description: "Notes" })])

    renderPanel()
    await openEditMode(user)

    const textarea = screen.getByLabelText("Task description")
    fireEvent.keyDown(textarea, { key: "Enter" })

    // The form stays open and nothing is saved: Enter belongs to the text.
    expect(screen.getByLabelText("Task description")).toBeInTheDocument()
    expect(updateTask).not.toHaveBeenCalled()
  })

  it("caps the description at the 1000-character column limit, silently", async () => {
    const user = userEvent.setup()
    setTasks([makeTask({ title: "Task one" })])

    renderPanel()
    await openEditMode(user)

    // maxLength is the whole enforcement: the browser stops typing and
    // truncates pastes, and no validation copy exists to find.
    expect(screen.getByLabelText("Task description")).toHaveAttribute(
      "maxlength",
      "1000"
    )
  })

  it("cancels an edit on Escape without saving or reaching the case panel", async () => {
    const user = userEvent.setup()
    const outerKeyDown = jest.fn()
    setTasks([makeTask({ title: "Task one" })])

    renderPanel(outerKeyDown)
    await openEditMode(user)
    outerKeyDown.mockClear()

    const input = screen.getByLabelText("Task title")
    fireEvent.change(input, { target: { value: "Discarded" } })
    fireEvent.keyDown(input, { key: "Escape" })

    expect(updateTask).not.toHaveBeenCalled()
    expect(screen.queryByLabelText("Task title")).not.toBeInTheDocument()
    expect(screen.getByText("Task one")).toBeInTheDocument()
    // stopPropagation: a propagated Escape would close the whole case panel
    // in slideover/embedded mode instead of just the edit.
    expect(outerKeyDown).not.toHaveBeenCalled()
  })

  it("keeps at most one row in edit mode at a time", async () => {
    const user = userEvent.setup()
    setTasks([
      makeTask({ id: "t1", title: "First task" }),
      makeTask({ id: "t2", title: "Second task" }),
    ])

    renderPanel()

    // Edit the first row…
    await openEditMode(user, "First task")
    expect(screen.getByLabelText("Task title")).toHaveValue("First task")

    // …then the second: the first must fall back to read mode.
    await openEditMode(user, "Second task")

    const titleInputs = screen.getAllByLabelText("Task title")
    expect(titleInputs).toHaveLength(1)
    expect(titleInputs[0]).toHaveValue("Second task")
    expect(screen.getByText("First task")).toBeInTheDocument()
  })

  it("carries no ⋯ menu: right-click is the row's only menu", () => {
    setTasks([makeTask({ title: "Task one" })])

    renderPanel()

    expect(
      screen.queryByRole("button", { name: "Task options" })
    ).not.toBeInTheDocument()
  })

  // --- Context menu -------------------------------------------------------

  it("keeps all four field submenus plus Edit task and Delete on right-click", async () => {
    setTasks([makeTask({ title: "Task one" })])

    renderPanel()
    fireEvent.contextMenu(screen.getByText("Task one"))

    expect(
      await screen.findByRole("menuitem", { name: /change status/i })
    ).toBeInTheDocument()
    expect(
      screen.getByRole("menuitem", { name: /assign to/i })
    ).toBeInTheDocument()
    expect(
      screen.getByRole("menuitem", { name: /set priority/i })
    ).toBeInTheDocument()
    expect(
      screen.getByRole("menuitem", { name: /set workflow/i })
    ).toBeInTheDocument()
    expect(
      screen.getByRole("menuitem", { name: /edit task/i })
    ).toBeInTheDocument()
    expect(
      screen.getByRole("menuitem", { name: /delete/i })
    ).toBeInTheDocument()
  })

  it("deletes through the context menu behind the retained confirm", async () => {
    setTasks([makeTask({ title: "Task one" })])

    renderPanel()
    fireEvent.contextMenu(screen.getByText("Task one"))
    fireEvent.click(await screen.findByRole("menuitem", { name: /delete/i }))

    const confirm = await screen.findByRole("alertdialog")
    expect(confirm).toHaveTextContent("Task one")
    fireEvent.click(screen.getByRole("button", { name: "Delete" }))
    expect(deleteTask).toHaveBeenCalledTimes(1)
  })

  // --- The new-task composer --------------------------------------------------

  it("shows the add-task ghost row as the only empty state", () => {
    renderPanel()

    expect(screen.getByRole("button", { name: "Add task" })).toBeInTheDocument()
    expect(screen.queryByText("No tasks")).not.toBeInTheDocument()
  })

  it("swaps the ghost row for the new-task card and focuses the title", () => {
    renderPanel()

    fireEvent.click(screen.getByRole("button", { name: "Add task" }))

    const title = screen.getByPlaceholderText("Task title")
    expect(title).toHaveFocus()
    expect(
      screen.queryByRole("button", { name: "Add task" })
    ).not.toBeInTheDocument()
  })

  it("creates on Enter with exactly the six form keys, then resets for rapid entry", async () => {
    const user = userEvent.setup()
    renderPanel()

    fireEvent.click(screen.getByRole("button", { name: "Add task" }))
    await user.keyboard("Triage the alert{Enter}")

    await waitFor(() => expect(createTask).toHaveBeenCalledTimes(1))
    const payload = createTask.mock.calls[0][0]
    expect(payload).toEqual({
      title: "Triage the alert",
      description: "",
      status: "todo",
      priority: "unknown",
      assignee_id: null,
      workflow_id: null,
    })
    // default_trigger_values must never ride along — sending it as null
    // erases trigger defaults written by WorkflowTriggerDialog.
    expect("default_trigger_values" in payload).toBe(false)

    // The card stays open, reset, with focus back in the title.
    const title = screen.getByPlaceholderText("Task title")
    await waitFor(() => expect(title).toHaveValue(""))
    expect(title).toHaveFocus()
  })

  it("creates with a workflow picked from the card's workflow pill", async () => {
    const user = userEvent.setup()
    renderPanel()

    fireEvent.click(screen.getByRole("button", { name: "Add task" }))
    await user.keyboard("Run containment")
    fireEvent.click(screen.getByRole("button", { name: "Workflow" }))
    fireEvent.click(
      await screen.findByRole("option", { name: /containment workflow/i })
    )
    fireEvent.click(screen.getByRole("button", { name: "Create task" }))

    await waitFor(() => expect(createTask).toHaveBeenCalledTimes(1))
    expect(createTask.mock.calls[0][0]).toMatchObject({
      title: "Run containment",
      workflow_id: "wf-1",
    })
  })

  it("keeps the new-task card open when a palette consumes its Escape", async () => {
    renderPanel()

    fireEvent.click(screen.getByRole("button", { name: "Add task" }))
    fireEvent.click(screen.getByRole("button", { name: "Workflow" }))
    const search = await screen.findByPlaceholderText("Set workflow…")
    fireEvent.keyDown(search, { key: "Escape" })

    // Radix portals the palette, but React synthetic events still bubble
    // through the React tree — the palette must consume its own Escape
    // instead of letting it cancel the card underneath.
    await waitFor(() =>
      expect(
        screen.queryByPlaceholderText("Set workflow…")
      ).not.toBeInTheDocument()
    )
    expect(screen.getByPlaceholderText("Task title")).toBeInTheDocument()
  })

  it("cancels the new-task card on Escape without reaching the case panel", () => {
    const outerKeyDown = jest.fn()
    renderPanel(outerKeyDown)

    fireEvent.click(screen.getByRole("button", { name: "Add task" }))
    fireEvent.keyDown(screen.getByPlaceholderText("Task title"), {
      key: "Escape",
    })

    expect(screen.getByRole("button", { name: "Add task" })).toBeInTheDocument()
    // stopPropagation: in slideover/embedded mode a propagated Escape would
    // close the whole case panel, not just the card.
    expect(outerKeyDown).not.toHaveBeenCalled()
  })

  it("never closes the new-task card on blur, so picker clicks keep the draft", async () => {
    const user = userEvent.setup()
    renderPanel()

    fireEvent.click(screen.getByRole("button", { name: "Add task" }))
    await user.keyboard("Draft in progress")
    fireEvent.blur(screen.getByPlaceholderText("Task title"))

    expect(screen.getByPlaceholderText("Task title")).toHaveValue(
      "Draft in progress"
    )
  })
})
