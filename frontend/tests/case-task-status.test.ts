/**
 * @jest-environment jsdom
 */

import type { CaseTaskRead } from "@/client"
import {
  getCaseTaskProgress,
  isCaseTaskDone,
  sortCaseTasksByTitle,
  sortCaseTasksByUrgency,
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

describe("sortCaseTasksByTitle", () => {
  it("orders titles alphabetically, ignoring case and reading numbers as numbers", () => {
    const tasks = [
      makeTask({ id: "t1", title: "Task 10" }),
      makeTask({ id: "t2", title: "bravo" }),
      makeTask({ id: "t3", title: "Task 2" }),
      makeTask({ id: "t4", title: "Alpha" }),
    ]

    expect(sortCaseTasksByTitle(tasks).map((task) => task.title)).toEqual([
      "Alpha",
      "bravo",
      "Task 2",
      "Task 10",
    ])
  })

  it("breaks title ties by creation time, then id", () => {
    const tasks = [
      makeTask({ id: "t3", created_at: "2024-01-02T00:00:00Z" }),
      makeTask({ id: "t2", created_at: "2024-01-01T00:00:00Z" }),
      makeTask({ id: "t1", created_at: "2024-01-01T00:00:00Z" }),
    ]

    expect(sortCaseTasksByTitle(tasks).map((task) => task.id)).toEqual([
      "t1",
      "t2",
      "t3",
    ])
  })

  it("does not mutate its input", () => {
    const tasks = [
      makeTask({ id: "t1", title: "Bravo" }),
      makeTask({ id: "t2", title: "Alpha" }),
    ]

    sortCaseTasksByTitle(tasks)

    expect(tasks.map((task) => task.id)).toEqual(["t1", "t2"])
  })
})

describe("sortCaseTasksByUrgency", () => {
  it("keeps status and priority ranks ahead of the title", () => {
    const tasks = [
      makeTask({ id: "t1", title: "Alpha", status: "completed" }),
      makeTask({ id: "t2", title: "Charlie", priority: "low" }),
      makeTask({ id: "t3", title: "Delta", priority: "high" }),
      makeTask({ id: "t4", title: "Bravo", priority: "low" }),
    ]

    expect(sortCaseTasksByUrgency(tasks).map((task) => task.title)).toEqual([
      "Delta",
      "Bravo",
      "Charlie",
      "Alpha",
    ])
  })
})
