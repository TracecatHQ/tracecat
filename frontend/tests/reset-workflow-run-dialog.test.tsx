import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { WorkflowExecutionResetPointRead } from "@/client"
import {
  formatResetPointPrimaryLabel,
  formatResetPointSecondaryLabel,
  ResetWorkflowRunDialog,
} from "@/components/workflow-runs/reset-workflow-run-dialog"

const RESET_POINTS: WorkflowExecutionResetPointRead[] = [
  {
    event_id: 4,
    event_time: "2026-01-01T00:00:00Z",
    event_type: "WORKFLOW_TASK_COMPLETED",
    label: "Workflow start",
    is_start: true,
    is_resettable: true,
  },
  {
    event_id: 8,
    event_time: "2026-01-01T00:00:02Z",
    event_type: "WORKFLOW_TASK_COMPLETED",
    label: "After Action A",
    action_ref: "action_a",
    action_name: "core.transform.reshape",
    action_event_id: 7,
    action_relation: "after",
    is_start: false,
    is_resettable: true,
  },
  {
    event_id: 12,
    event_time: "2026-01-01T00:00:04Z",
    event_type: "WORKFLOW_TASK_COMPLETED",
    label: "Event 12",
    is_start: false,
    is_resettable: true,
  },
]

beforeAll(() => {
  HTMLElement.prototype.hasPointerCapture ??= () => false
  HTMLElement.prototype.setPointerCapture ??= () => {}
  HTMLElement.prototype.releasePointerCapture ??= () => {}
  HTMLElement.prototype.scrollIntoView ??= () => {}
})

describe("ResetWorkflowRunDialog", () => {
  it("formats action-aware reset labels", () => {
    expect(formatResetPointPrimaryLabel(RESET_POINTS[1])).toBe("After Action A")
    expect(formatResetPointSecondaryLabel(RESET_POINTS[1])).toBe("Event 8")
  })

  it("formats unattributed reset points as advanced checkpoints", () => {
    const fallbackPoint: WorkflowExecutionResetPointRead = {
      event_id: 12,
      event_time: "2026-01-01T00:00:04Z",
      event_type: "WORKFLOW_TASK_COMPLETED",
      label: "Event 12",
      is_start: false,
      is_resettable: true,
    }

    expect(formatResetPointPrimaryLabel(fallbackPoint)).toBe(
      "Workflow checkpoint"
    )
    expect(formatResetPointSecondaryLabel(fallbackPoint)).toBe(
      "Event 12 · system checkpoint"
    )
  })

  it("submits workflow start as an empty event ID", async () => {
    const onSubmit = jest.fn().mockResolvedValue(undefined)

    render(
      <ResetWorkflowRunDialog
        open={true}
        onOpenChange={() => {}}
        executionCount={1}
        resetPoints={RESET_POINTS}
        resetPointsLoading={false}
        isSubmitting={false}
        onSubmit={onSubmit}
      />
    )

    await userEvent.click(screen.getByRole("button", { name: "Reset" }))

    expect(onSubmit).toHaveBeenCalledWith({
      eventId: null,
      reason: null,
      reapplyType: "all_eligible",
    })
  })

  it("shows action-aware reset labels with temporal event IDs", async () => {
    const user = userEvent.setup()
    const onSubmit = jest.fn().mockResolvedValue(undefined)

    render(
      <ResetWorkflowRunDialog
        open={true}
        onOpenChange={() => {}}
        executionCount={1}
        resetPoints={RESET_POINTS}
        resetPointsLoading={false}
        isSubmitting={false}
        onSubmit={onSubmit}
      />
    )

    await user.click(screen.getByRole("combobox", { name: "Reset point" }))
    const actionOption = await screen.findByText("After Action A")
    expect(screen.getByText("Event 8")).toBeInTheDocument()
    await user.click(actionOption)

    await user.click(screen.getByRole("button", { name: "Reset" }))

    expect(onSubmit).toHaveBeenCalledWith({
      eventId: 8,
      reason: null,
      reapplyType: "all_eligible",
    })
  })

  it("groups unattributed reset points under advanced checkpoints", async () => {
    const user = userEvent.setup()

    render(
      <ResetWorkflowRunDialog
        open={true}
        onOpenChange={() => {}}
        executionCount={1}
        resetPoints={RESET_POINTS}
        resetPointsLoading={false}
        isSubmitting={false}
        onSubmit={jest.fn().mockResolvedValue(undefined)}
      />
    )

    await user.click(screen.getByRole("combobox", { name: "Reset point" }))

    expect(screen.getByText("Recommended reset points")).toBeInTheDocument()
    expect(screen.getByText("Advanced checkpoints")).toBeInTheDocument()
    expect(screen.getByText("Workflow checkpoint")).toBeInTheDocument()
    expect(screen.getByText("Event 12 · system checkpoint")).toBeInTheDocument()
  })
})
