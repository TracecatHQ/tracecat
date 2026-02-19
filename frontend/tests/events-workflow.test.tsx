/**
 * @jest-environment jsdom
 */

import { render } from "@testing-library/react"
import type { WorkflowExecutionEventStatus } from "@/client"
import { getWorkflowEventIcon } from "@/components/events/workflow-event-status"

// Mock the Lucide React icons
jest.mock("lucide-react", () => ({
  AlertTriangleIcon: ({
    className,
    strokeWidth,
  }: {
    className?: string
    strokeWidth?: number
  }) => (
    <div
      data-testid="alert-triangle-icon"
      className={className}
      data-stroke-width={strokeWidth}
    />
  ),
  CircleX: ({ className }: { className?: string }) => (
    <div data-testid="circle-x-icon" className={className} />
  ),
  CircleCheck: ({ className }: { className?: string }) => (
    <div data-testid="circle-check-icon" className={className} />
  ),
  CircleMinusIcon: ({ className }: { className?: string }) => (
    <div data-testid="circle-minus-icon" className={className} />
  ),
  AlarmClockOffIcon: ({
    className,
    strokeWidth,
  }: {
    className?: string
    strokeWidth?: number
  }) => (
    <div
      data-testid="alarm-clock-off-icon"
      className={className}
      data-stroke-width={strokeWidth}
    />
  ),
  GitForkIcon: ({
    className,
    strokeWidth,
  }: {
    className?: string
    strokeWidth?: number
  }) => (
    <div
      data-testid="git-fork-icon"
      className={className}
      data-stroke-width={strokeWidth}
    />
  ),
}))

// Mock the Spinner component
jest.mock("@/components/loading/spinner", () => ({
  Spinner: ({ className }: { className?: string }) => (
    <div data-testid="spinner" className={className} />
  ),
}))

// Mock cn utility
jest.mock("@/lib/utils", () => ({
  cn: jest.fn((...classes: (string | undefined)[]) =>
    classes.filter(Boolean).join(" ")
  ),
  undoSlugify: jest.fn((str: string) => str),
  slugify: jest.fn((str: string) => str),
  slugifyActionRef: jest.fn((str: string) => str),
}))

// Mock complex dependency chains that cause issues
jest.mock("@/lib/api", () => ({
  getBaseUrl: jest.fn(() => "http://localhost:3000"),
}))

jest.mock("@/lib/auth", () => ({}))
jest.mock("@/hooks/use-auth", () => ({}))
jest.mock("@/providers/workspace-id", () => ({}))
jest.mock("@/components/executions/nav", () => ({}))

// Mock React JSX runtime to return renderable elements
jest.mock("react/jsx-runtime", () => {
  const React = require("react")
  return {
    jsx: jest.fn((type, props, key) =>
      React.createElement(type, { ...props, key })
    ),
    jsxs: jest.fn((type, props, key) =>
      React.createElement(type, { ...props, key })
    ),
  }
})

describe("events-workflow", () => {
  describe("getWorkflowEventIcon", () => {
    it("should return CircleX for FAILED status", () => {
      const icon = getWorkflowEventIcon(
        "FAILED" as WorkflowExecutionEventStatus,
        "custom-class"
      )

      const { container } = render(<div>{icon}</div>)
      const circleXIcon = container.querySelector(
        '[data-testid="circle-x-icon"]'
      )

      expect(circleXIcon).toBeTruthy()
      expect(circleXIcon?.getAttribute("class")).toContain(
        "fill-rose-500 stroke-white custom-class"
      )
    })

    it("should return Spinner for SCHEDULED status", () => {
      const icon = getWorkflowEventIcon(
        "SCHEDULED" as WorkflowExecutionEventStatus,
        "custom-class"
      )

      const { container } = render(<div>{icon}</div>)
      expect(container.querySelector('[data-testid="spinner"]')).toBeTruthy()
    })

    it("should return Spinner for STARTED status", () => {
      const icon = getWorkflowEventIcon(
        "STARTED" as WorkflowExecutionEventStatus,
        "custom-class"
      )

      const { container } = render(<div>{icon}</div>)
      expect(container.querySelector('[data-testid="spinner"]')).toBeTruthy()
    })

    it("should return CircleCheck for COMPLETED status", () => {
      const icon = getWorkflowEventIcon(
        "COMPLETED" as WorkflowExecutionEventStatus,
        "custom-class"
      )

      const { container } = render(<div>{icon}</div>)
      expect(
        container.querySelector('[data-testid="circle-check-icon"]')
      ).toBeTruthy()
    })

    it("should return CircleMinusIcon for CANCELED status", () => {
      const icon = getWorkflowEventIcon(
        "CANCELED" as WorkflowExecutionEventStatus,
        "custom-class"
      )

      const { container } = render(<div>{icon}</div>)
      const circleMinusIcon = container.querySelector(
        '[data-testid="circle-minus-icon"]'
      )

      expect(circleMinusIcon).toBeTruthy()
      expect(circleMinusIcon?.getAttribute("class")).toContain(
        "fill-orange-500 stroke-white custom-class"
      )
    })

    it("should return CircleMinusIcon for TERMINATED status", () => {
      const icon = getWorkflowEventIcon(
        "TERMINATED" as WorkflowExecutionEventStatus,
        "custom-class"
      )

      const { container } = render(<div>{icon}</div>)
      const circleMinusIcon = container.querySelector(
        '[data-testid="circle-minus-icon"]'
      )

      expect(circleMinusIcon).toBeTruthy()
      expect(circleMinusIcon?.getAttribute("class")).toContain(
        "fill-rose-500 stroke-white custom-class"
      )
    })

    it("should return AlarmClockOffIcon for TIMED_OUT status", () => {
      const icon = getWorkflowEventIcon(
        "TIMED_OUT" as WorkflowExecutionEventStatus,
        "custom-class"
      )

      const { container } = render(<div>{icon}</div>)
      const alarmIcon = container.querySelector(
        '[data-testid="alarm-clock-off-icon"]'
      )

      expect(alarmIcon).toBeTruthy()
      expect(alarmIcon?.getAttribute("class")).toContain(
        "!size-3 stroke-rose-500 custom-class"
      )
      expect(alarmIcon?.getAttribute("data-stroke-width")).toBe("2.5")
    })

    it("should return GitForkIcon for DETACHED status", () => {
      const icon = getWorkflowEventIcon(
        "DETACHED" as WorkflowExecutionEventStatus,
        "custom-class"
      )

      const { container } = render(<div>{icon}</div>)
      const gitForkIcon = container.querySelector(
        '[data-testid="git-fork-icon"]'
      )

      expect(gitForkIcon).toBeTruthy()
      expect(gitForkIcon?.getAttribute("class")).toContain(
        "!size-3 stroke-emerald-500 custom-class"
      )
      expect(gitForkIcon?.getAttribute("data-stroke-width")).toBe("2.5")
    })

    it("should return CircleX for UNKNOWN status", () => {
      const icon = getWorkflowEventIcon(
        "UNKNOWN" as WorkflowExecutionEventStatus,
        "custom-class"
      )

      const { container } = render(<div>{icon}</div>)
      const circleXIcon = container.querySelector(
        '[data-testid="circle-x-icon"]'
      )

      expect(circleXIcon).toBeTruthy()
      expect(circleXIcon?.getAttribute("class")).toContain(
        "fill-rose-500 stroke-white custom-class"
      )
    })

    it("should throw error for invalid status", () => {
      expect(() => {
        getWorkflowEventIcon("INVALID_STATUS" as WorkflowExecutionEventStatus)
      }).toThrow("Invalid status")
    })
  })
})
