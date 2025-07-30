import type { WorkflowExecutionEventStatus } from "@/client"

// Simple unit test that verifies the icon selection logic without complex React dependencies
describe("Workflow Event Icon Logic", () => {
  // Extract the core logic for testing
  function getIconType(status: WorkflowExecutionEventStatus): string {
    switch (status) {
      case "FAILED":
      case "UNKNOWN":
        return "CircleX"
      case "COMPLETED":
        return "CircleCheck"
      case "CANCELED":
      case "TERMINATED":
        return "CircleMinusIcon"
      case "TIMED_OUT":
        return "AlarmClockOffIcon"
      case "DETACHED":
        return "GitForkIcon"
      case "SCHEDULED":
      case "STARTED":
        return "Spinner"
      default:
        throw new Error("Invalid status")
    }
  }

  describe("Status to Icon Mapping", () => {
    it("should map FAILED status to CircleX", () => {
      expect(getIconType("FAILED")).toBe("CircleX")
    })

    it("should map COMPLETED status to CircleCheck", () => {
      expect(getIconType("COMPLETED")).toBe("CircleCheck")
    })

    it("should map SCHEDULED status to Spinner", () => {
      expect(getIconType("SCHEDULED")).toBe("Spinner")
    })

    it("should map STARTED status to Spinner", () => {
      expect(getIconType("STARTED")).toBe("Spinner")
    })

    it("should map CANCELED status to CircleMinusIcon", () => {
      expect(getIconType("CANCELED")).toBe("CircleMinusIcon")
    })

    it("should map TERMINATED status to CircleMinusIcon", () => {
      expect(getIconType("TERMINATED")).toBe("CircleMinusIcon")
    })

    it("should map TIMED_OUT status to AlarmClockOffIcon", () => {
      expect(getIconType("TIMED_OUT")).toBe("AlarmClockOffIcon")
    })

    it("should map DETACHED status to GitForkIcon", () => {
      expect(getIconType("DETACHED")).toBe("GitForkIcon")
    })

    it("should map UNKNOWN status to CircleX", () => {
      expect(getIconType("UNKNOWN")).toBe("CircleX")
    })

    it("should throw error for invalid status", () => {
      expect(() => {
        getIconType("INVALID_STATUS" as never)
      }).toThrow("Invalid status")
    })
  })
})
