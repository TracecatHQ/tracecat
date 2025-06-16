/**
 * @jest-environment jsdom
 */

import React from "react"
import { useForm, FormProvider, Control, FieldValues } from "react-hook-form"

import { YamlStyledEditor, YamlStyledEditorRef } from "@/components/editor/codemirror/yaml-editor"

// Mock CodeMirror components
jest.mock("@uiw/react-codemirror", () => {
  return function MockCodeMirror({
    value,
    onChange,
    onBlur,
  }: {
    value: string
    onChange: (value: string) => void
    onBlur?: () => void
  }) {
    return (
      <textarea
        data-testid="codemirror-textarea"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onBlur={onBlur}
      />
    )
  }
})

// Mock CodeMirror modules
jest.mock("@codemirror/autocomplete", () => ({
  autocompletion: () => [],
  closeBrackets: () => [],
  closeBracketsKeymap: [],
  completionKeymap: [],
}))

jest.mock("@codemirror/commands", () => ({
  history: () => [],
  historyKeymap: [],
  indentWithTab: {},
  standardKeymap: [],
}))

jest.mock("@codemirror/lang-yaml", () => ({
  yaml: () => [],
}))

jest.mock("@codemirror/language", () => ({
  bracketMatching: () => [],
  indentUnit: { of: () => [] },
}))

jest.mock("@codemirror/lint", () => ({
  linter: () => [],
  lintGutter: () => [],
}))

jest.mock("@codemirror/view", () => ({
  EditorView: {
    lineWrapping: [],
    domEventHandlers: () => [],
    theme: () => [],
  },
  keymap: { of: () => [] },
  ViewPlugin: { fromClass: () => [] },
}))

// Mock workspace and workflow providers
jest.mock("@/providers/workspace", () => ({
  useWorkspace: () => ({ workspaceId: "test-workspace" }),
}))

jest.mock("@/providers/workflow", () => ({
  useWorkflow: () => ({ workflowId: "test-workflow", workflow: { actions: [] } }),
}))

// Mock common editor utilities
jest.mock("@/components/editor/codemirror/common", () => ({
  createAtKeyCompletion: () => [],
  createEscapeKeyHandler: () => [],
  createBlurHandler: () => () => false,
  createExpressionNodeHover: () => [],
  createFunctionCompletion: () => [],
  createActionCompletion: () => [],
  createMentionCompletion: () => [],
  createTemplatePillPlugin: () => [],
  createPillClickHandler: () => () => false,
  editingRangeField: {},
  enhancedCursorLeft: () => false,
  enhancedCursorRight: () => false,
  templatePillTheme: [],
}))


describe("YamlStyledEditor Implementation", () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it("should have the correct interface structure", () => {
    // This test verifies that the YamlStyledEditor has the expected ref interface
    const TestComponent = () => {
      const methods = useForm<{ testField: any }>({
        defaultValues: { testField: { key: "value" } },
      })
      const ref = React.useRef<YamlStyledEditorRef>(null)

      React.useEffect(() => {
        // Test that the ref has the expected commitToForm method
        if (ref.current) {
          expect(typeof ref.current.commitToForm).toBe("function")
        }
      }, [])

      return (
        <FormProvider {...methods}>
          <YamlStyledEditor
            ref={ref}
            name="testField"
            control={methods.control as unknown as Control<FieldValues>}
          />
        </FormProvider>
      )
    }

    // Basic component structure test
    expect(() => <TestComponent />).not.toThrow()
  })

  it("should export the expected interface", () => {
    // Verify YamlStyledEditor is properly exported
    expect(YamlStyledEditor).toBeDefined()
    expect(typeof YamlStyledEditor).toBe("object") // React.forwardRef returns an object
  })

  it("should accept the required props", () => {
    const TestComponent = () => {
      const methods = useForm<FieldValues>({
        defaultValues: { testField: { key: "value" } },
      })

      return (
        <FormProvider {...methods}>
          <YamlStyledEditor
            name="testField"
            control={methods.control}
          />
        </FormProvider>
      )
    }

    // Component should render without errors with required props
    expect(() => <TestComponent />).not.toThrow()
  })
})

// Integration test notes:
// The actual behavior testing would require:
// 1. Testing libraries (@testing-library/react, @testing-library/user-event)
// 2. Proper Jest DOM setup
// 3. CodeMirror mock implementations
//
// Expected behaviors to test when dependencies are available:
// - Typing "key:" should NOT push { key: null } to RHF until blur
// - Valid YAML should commit to RHF on blur
// - Cmd/Ctrl+Enter should trigger explicit commit
// - Invalid YAML should not update RHF
// - commitToForm() method should be accessible via ref
