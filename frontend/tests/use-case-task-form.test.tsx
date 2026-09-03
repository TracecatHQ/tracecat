/**
 * @jest-environment jsdom
 */

import { act, renderHook } from "@testing-library/react"
import type { CaseTaskRead } from "@/client"
import {
  caseTaskFormDefaults,
  caseTaskFormValuesFromTask,
  useCaseTaskForm,
} from "@/hooks/use-case-task-form"
import { useCreateCaseTask, useUpdateCaseTask } from "@/lib/hooks"

jest.mock("@/lib/hooks", () => ({
  useCreateCaseTask: jest.fn(),
  useUpdateCaseTask: jest.fn(),
}))

const mockUseCreateCaseTask = useCreateCaseTask as jest.MockedFunction<
  typeof useCreateCaseTask
>
const mockUseUpdateCaseTask = useUpdateCaseTask as jest.MockedFunction<
  typeof useUpdateCaseTask
>

const SIX_FORM_KEYS = [
  "assignee_id",
  "description",
  "priority",
  "status",
  "title",
  "workflow_id",
]

function makeTask(overrides: Partial<CaseTaskRead> = {}): CaseTaskRead {
  return {
    id: "task-1",
    created_at: "2024-01-01T00:00:00Z",
    updated_at: "2024-01-01T00:00:00Z",
    case_id: "case-1",
    title: "Existing task",
    description: "Existing notes",
    priority: "high",
    status: "in_progress",
    assignee: null,
    workflow_id: "wf-1",
    // Present on the task, and must never reach a mutation payload.
    default_trigger_values: { alert_id: "a-1" },
    ...overrides,
  }
}

describe("caseTaskFormDefaults", () => {
  it("returns blank create-mode defaults", () => {
    expect(caseTaskFormDefaults()).toEqual({
      title: "",
      description: "",
      status: "todo",
      priority: "unknown",
      assignee_id: null,
      workflow_id: null,
    })
  })
})

describe("caseTaskFormValuesFromTask", () => {
  it("lifts exactly the six form fields from the task", () => {
    expect(caseTaskFormValuesFromTask(makeTask())).toEqual({
      title: "Existing task",
      description: "Existing notes",
      status: "in_progress",
      priority: "high",
      assignee_id: null,
      workflow_id: "wf-1",
    })
  })

  it("normalizes a null description to an empty string", () => {
    expect(caseTaskFormValuesFromTask(makeTask({ description: null }))).toEqual(
      expect.objectContaining({ description: "" })
    )
  })
})

describe("useCaseTaskForm", () => {
  let createTask: jest.Mock
  let updateTask: jest.Mock

  beforeEach(() => {
    createTask = jest.fn((_payload, options) => options?.onSuccess?.())
    updateTask = jest.fn((_payload, options) => options?.onSuccess?.())
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
  })

  it("creates with exactly the six form keys and never default_trigger_values", async () => {
    const onCreateSuccess = jest.fn()
    const { result } = renderHook(() =>
      useCaseTaskForm({
        caseId: "case-1",
        workspaceId: "ws-1",
        onCreateSuccess,
      })
    )

    act(() => {
      result.current.form.setValue("title", "New task")
    })
    await act(async () => {
      await result.current.submit()
    })

    expect(createTask).toHaveBeenCalledTimes(1)
    const payload = createTask.mock.calls[0][0]
    expect(payload).toEqual({
      title: "New task",
      description: "",
      status: "todo",
      priority: "unknown",
      assignee_id: null,
      workflow_id: null,
    })
    // Sending default_trigger_values — even as null — would erase trigger
    // defaults written by WorkflowTriggerDialog.
    expect(Object.keys(payload).sort()).toEqual(SIX_FORM_KEYS)
    expect("default_trigger_values" in payload).toBe(false)
    expect(onCreateSuccess).toHaveBeenCalledTimes(1)
    expect(updateTask).not.toHaveBeenCalled()
  })

  it("resets the form after a successful create for rapid entry", async () => {
    const { result } = renderHook(() =>
      useCaseTaskForm({ caseId: "case-1", workspaceId: "ws-1" })
    )

    act(() => {
      result.current.form.setValue("title", "First task")
    })
    await act(async () => {
      await result.current.submit()
    })

    expect(result.current.form.getValues("title")).toBe("")
  })

  it("does not create when the title is empty", async () => {
    const { result } = renderHook(() =>
      useCaseTaskForm({ caseId: "case-1", workspaceId: "ws-1" })
    )

    await act(async () => {
      await result.current.submit()
    })

    expect(createTask).not.toHaveBeenCalled()
  })

  it("seeds edit mode from the task and updates with exactly the six keys", async () => {
    const onUpdateSuccess = jest.fn()
    const { result } = renderHook(() =>
      useCaseTaskForm({
        caseId: "case-1",
        workspaceId: "ws-1",
        task: makeTask(),
        onUpdateSuccess,
      })
    )

    expect(result.current.form.getValues()).toEqual(
      caseTaskFormValuesFromTask(makeTask())
    )

    act(() => {
      result.current.form.setValue("title", "Renamed task")
    })
    await act(async () => {
      await result.current.submit()
    })

    expect(updateTask).toHaveBeenCalledTimes(1)
    const payload = updateTask.mock.calls[0][0]
    expect(payload).toEqual({
      title: "Renamed task",
      description: "Existing notes",
      status: "in_progress",
      priority: "high",
      assignee_id: null,
      workflow_id: "wf-1",
    })
    // The edited task carries default_trigger_values, and it still must not
    // ride along — sending it, even unchanged, is how it gets erased.
    expect(Object.keys(payload).sort()).toEqual(SIX_FORM_KEYS)
    expect("default_trigger_values" in payload).toBe(false)
    expect(onUpdateSuccess).toHaveBeenCalledTimes(1)
    expect(createTask).not.toHaveBeenCalled()
  })

  it("does not update when the title is emptied", async () => {
    const { result } = renderHook(() =>
      useCaseTaskForm({
        caseId: "case-1",
        workspaceId: "ws-1",
        task: makeTask(),
      })
    )

    act(() => {
      result.current.form.setValue("title", "   ")
    })
    await act(async () => {
      await result.current.submit()
    })

    expect(updateTask).not.toHaveBeenCalled()
  })
})
