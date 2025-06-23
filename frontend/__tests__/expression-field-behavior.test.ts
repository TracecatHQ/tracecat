import { createTemplateRegex } from "@/lib/expressions"

/**
 * Check if a value contains a template expression pattern
 */
function isExpression(value: unknown): boolean {
  if (typeof value !== "string") {
    return false
  }
  const regex = createTemplateRegex()
  return regex.test(value)
}

describe("Expression Field Behavior", () => {
  describe("isExpression utility", () => {
    it("should detect valid template expressions", () => {
      expect(isExpression("${{ ACTIONS.test.result }}")).toBe(true)
      expect(isExpression("${{ FN.add(1, 2) }}")).toBe(true)
      expect(isExpression("${{ inputs.username }}")).toBe(true)
      expect(isExpression("${{ SECRETS.api.token }}")).toBe(true)
      expect(
        isExpression("Some text with ${{ ACTIONS.test.result }} expression")
      ).toBe(true)
    })

    it("should not detect non-expression values", () => {
      expect(isExpression("true")).toBe(false)
      expect(isExpression("false")).toBe(false)
      expect(isExpression("hello world")).toBe(false)
      expect(isExpression("123")).toBe(false)
      expect(isExpression("")).toBe(false)
      expect(isExpression(true)).toBe(false)
      expect(isExpression(false)).toBe(false)
      expect(isExpression(123)).toBe(false)
      expect(isExpression(null)).toBe(false)
      expect(isExpression(undefined)).toBe(false)
    })

    it("should handle malformed or partial expressions", () => {
      expect(isExpression("${ incomplete")).toBe(false)
      expect(isExpression("{{ missing dollar }}")).toBe(false)
      expect(isExpression("${{ missing closing")).toBe(false)
      expect(isExpression("missing opening }}")).toBe(false)
    })

    it("should handle empty or whitespace-only expressions", () => {
      expect(isExpression("${{}}")).toBe(true) // Empty expression is still valid syntax
      expect(isExpression("${{ }}")).toBe(true) // Whitespace-only expression
      expect(isExpression("${{   }}")).toBe(true) // Multiple spaces
    })
  })

  describe("Field type rendering logic", () => {
    it("should prioritize expression rendering for expression values", () => {
      // This would be testing the PolymorphicField logic
      // For a boolean field with an expression value:
      const fieldValue = "${{ ACTIONS.test.result }}"
      const _schemaType = "boolean"

      // The logic should render as expression, not checkbox
      expect(isExpression(fieldValue)).toBe(true)

      // For a boolean field with a literal value:
      const literalValue = "true"
      expect(isExpression(literalValue)).toBe(false)
    })
  })
})
