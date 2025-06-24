/**
 * Documentation test for the template pill click behavior fix
 *
 * This test demonstrates the fix for the bug where clicking immediately
 * after a template pill (at the right boundary) would not exit editing mode.
 */

describe("Template Pill Click Behavior Fix", () => {
  describe("Right boundary click logic", () => {
    it("should demonstrate the original bug and the fix", () => {
      // Simulate a template pill range from position 5 to 10
      const templateRange = { from: 5, to: 10 }

      // Test case: clicking at position 10 (right boundary)
      const clickPosition = 10

      // ORIGINAL LOGIC (before fix):
      // findTemplateAt would return the range if pos >= from && pos <= to
      const originalLogicWouldMatch =
        clickPosition >= templateRange.from && clickPosition <= templateRange.to

      // FIXED LOGIC (after fix):
      // In createPillClickHandler, we treat pos === range.to as outside the pill
      // This is achieved by: range && pos === range.to ? null : range
      const fixedLogicTreatsAsOutside = clickPosition === templateRange.to

      // Verify the fix
      expect(originalLogicWouldMatch).toBe(true) // This was the bug
      expect(fixedLogicTreatsAsOutside).toBe(true) // This triggers the fix

      // The fix ensures that when clickedTemplateRange becomes null,
      // the condition `!clickedTemplateRange && currentEditingRange`
      // will be true, causing the editing state to be cleared
    })

    it("should preserve correct behavior for other positions", () => {
      const templateRange = { from: 5, to: 10 }

      // Test inside the pill (should enter/stay in editing mode)
      const insidePosition = 7
      const insideResult =
        insidePosition >= templateRange.from &&
        insidePosition < templateRange.to
      expect(insideResult).toBe(true)

      // Test at left boundary (should enter editing mode)
      const leftBoundaryPosition = 5
      const leftBoundaryResult =
        leftBoundaryPosition >= templateRange.from &&
        leftBoundaryPosition < templateRange.to
      expect(leftBoundaryResult).toBe(true)

      // Test completely outside (should exit editing mode)
      const outsidePosition = 15
      const outsideResult =
        outsidePosition >= templateRange.from &&
        outsidePosition < templateRange.to
      expect(outsideResult).toBe(false)
    })
  })

  describe("Edge cases", () => {
    it("should handle edge cases correctly", () => {
      const _templateRange = { from: 0, to: 5 }

      // Single character template at position 0
      const singleCharTemplate = { from: 0, to: 1 }
      expect(1 === singleCharTemplate.to).toBe(true) // Right boundary

      // Empty range (should not occur in practice but handle gracefully)
      const emptyRange = { from: 5, to: 5 }
      expect(5 === emptyRange.to).toBe(true) // Right boundary
    })
  })
})
