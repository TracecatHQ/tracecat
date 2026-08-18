/**
 * @jest-environment jsdom
 */

import type { CaseTaskRead } from "@/client"
import {
  getCaseTaskProgress,
  isCaseTaskDone,
} from "@/components/cases/case-task-status"

function makeTask(overrides: Partial<CaseTaskRead> = {}): CaseTaskRead {
  return {
    id: "task-1",
    created_at: "2024-01-01T00:00:00Z",
    updated_at: "2024-01-01T00:00:00Z",
    case_id: "case-1",
    title: "Task",
    description: null,
    priority: "unknown",
    status: "todo",
    assignee: null,
    workflow_id: null,
    ...overrides,
  }
}

describe("isCaseTaskDone", () => {
  it("counts only completed as done", () => {
    expect(isCaseTaskDone("completed")).toBe(true)
    expect(isCaseTaskDone("todo")).toBe(false)
    expect(isCaseTaskDone("in_progress")).toBe(false)
    // Blocked is outstanding work that needs attention, not done.
    expect(isCaseTaskDone("blocked")).toBe(false)
  })
})

describe("getCaseTaskProgress", () => {
  it("counts completed tasks against the full total", () => {
    const tasks = [
      makeTask({ id: "t1", status: "completed" }),
      makeTask({ id: "t2", status: "completed" }),
      makeTask({ id: "t3", status: "todo" }),
      makeTask({ id: "t4", status: "in_progress" }),
    ]

    expect(getCaseTaskProgress(tasks)).toEqual({ done: 2, total: 4 })
  })

  it("does not count blocked tasks as done", () => {
    const tasks = [
      makeTask({ id: "t1", status: "blocked" }),
      makeTask({ id: "t2", status: "completed" }),
    ]

    expect(getCaseTaskProgress(tasks)).toEqual({ done: 1, total: 2 })
  })

  it("returns zero for empty and undefined task lists", () => {
    expect(getCaseTaskProgress([])).toEqual({ done: 0, total: 0 })
    expect(getCaseTaskProgress(undefined)).toEqual({ done: 0, total: 0 })
  })
})
