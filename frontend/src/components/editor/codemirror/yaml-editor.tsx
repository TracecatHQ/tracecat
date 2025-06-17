"use client"

import React, { useCallback, useMemo, useRef, useState } from "react"
import { useWorkflow } from "@/providers/workflow"
import { useWorkspace } from "@/providers/workspace"
import {
  autocompletion,
  closeBrackets,
  closeBracketsKeymap,
  completionKeymap,
} from "@codemirror/autocomplete"
import {
  history,
  historyKeymap,
  indentWithTab,
  standardKeymap,
} from "@codemirror/commands"
import { yaml } from "@codemirror/lang-yaml"
import {
  bracketMatching,
  HighlightStyle,
  indentUnit,
  syntaxHighlighting,
} from "@codemirror/language"
import { linter, lintGutter, type Diagnostic } from "@codemirror/lint"
import { EditorState } from "@codemirror/state"
import {
  Decoration,
  DecorationSet,
  EditorView,
  keymap,
  ViewPlugin,
  type ViewUpdate,
} from "@codemirror/view"
import { tags } from "@lezer/highlight"
import CodeMirror from "@uiw/react-codemirror"
import { AlertTriangle, Check } from "lucide-react"
import { Control, FieldValues, useController } from "react-hook-form"
import YAML from "yaml"

import { cn } from "@/lib/utils"

import {
  createActionCompletion,
  createAtKeyCompletion,
  createBlurHandler,
  createEnvCompletion,
  createExitEditModeKeyHandler,
  createExpressionNodeHover,
  createFunctionCompletion,
  createMentionCompletion,
  createPillClickHandler,
  createPillDeleteKeymap,
  createSecretsCompletion,
  createTemplatePillPlugin,
  createVarCompletion,
  editingRangeField,
  EDITOR_STYLE,
  enhancedCursorLeft,
  enhancedCursorRight,
  setEditingRange,
  templatePillTheme,
} from "./common"

const stripNewline = (value: string) => {
  return value.endsWith("\n") ? value.slice(0, -1) : value
}

enum SaveState {
  IDLE = "idle",
  UNSAVED = "unsaved",
  SAVED = "saved",
  ERROR = "error",
}

export interface YamlStyledEditorRef {
  commitToForm: () => void
}

export const YamlStyledEditor = React.forwardRef<
  YamlStyledEditorRef,
  {
    name: string
    control: Control<FieldValues>
    forEachExpressions?: string | string[] | null | undefined
  }
>(({ name, control, forEachExpressions }, ref) => {
  const { field, fieldState } = useController<FieldValues>({
    name: name,
    control,
  })
  const { workspaceId } = useWorkspace()
  const { workflow } = useWorkflow()
  const [hasErrors, setHasErrors] = useState(false)
  const [saveState, setSaveState] = useState<SaveState>(SaveState.IDLE)
  const [validationErrors, setValidationErrors] = useState<string[]>([])
  const actions = workflow?.actions || []
  const editorRef = useRef<EditorView | null>(null)

  const textValue = React.useMemo(
    () => stripNewline(field.value ? YAML.stringify(field.value) : ""),
    [field.value]
  )
  // Internal editor value - always a string representation of the YAML
  const [buffer, setBuffer] = useState(textValue)

  // Sync external changes into the buffer
  React.useEffect(() => {
    setBuffer(textValue)
    setSaveState(SaveState.IDLE)
  }, [textValue])

  // Track if buffer differs from saved value
  React.useEffect(() => {
    if (buffer !== textValue && saveState !== SaveState.UNSAVED) {
      setSaveState(SaveState.UNSAVED)
    }
  }, [buffer, textValue, saveState])

  // Create stable refs for commitToForm to avoid extension recreation
  const bufferRef = useRef(buffer)
  const fieldRef = useRef(field)
  const saveStateRef = useRef(saveState)
  const setSaveStateRef = useRef(setSaveState)

  // Update refs when values change
  React.useEffect(() => {
    bufferRef.current = buffer
  }, [buffer])

  React.useEffect(() => {
    fieldRef.current = field
  }, [field])

  React.useEffect(() => {
    saveStateRef.current = saveState
  }, [saveState])

  React.useEffect(() => {
    setSaveStateRef.current = setSaveState
  }, [setSaveState])

  // Commit valid YAML to RHF (only when explicitly triggered)
  // Using refs to make this function stable and avoid extension recreation
  const commitToForm = useCallback(() => {
    try {
      const obj = YAML.parse(bufferRef.current)
      fieldRef.current.onChange(obj) // Push valid object to RHF
      setValidationErrors([])
      setHasErrors(false)
      return true
    } catch (err) {
      // Invalid YAML – don't update RHF, keep last valid value
      console.warn("YAML parse error on commit:", err)
      setValidationErrors([err instanceof Error ? err.message : "Invalid YAML"])
      return false
    }
  }, []) // No dependencies - stable function

  // Create ref for commitToForm so it can be used in keybindings
  const commitToFormRef = useRef(commitToForm)
  React.useEffect(() => {
    commitToFormRef.current = commitToForm
  }, [commitToForm])

  // Cleanup validation timeout on unmount
  React.useEffect(() => {
    return () => {
      if (validationTimeoutRef.current) {
        clearTimeout(validationTimeoutRef.current)
      }
    }
  }, [])

  // Debounced validation for visual feedback to reduce re-renders during typing
  const validateYaml = useCallback((text: string) => {
    try {
      YAML.parse(text)
      setValidationErrors([])
      return true
    } catch (err) {
      setValidationErrors([err instanceof Error ? err.message : "Invalid YAML"])
      return false
    }
  }, [])

  // Debounced validation ref to avoid excessive validation during typing
  const validationTimeoutRef = useRef<NodeJS.Timeout>()

  const debouncedValidateYaml = useCallback(
    (text: string) => {
      // Clear previous timeout
      if (validationTimeoutRef.current) {
        clearTimeout(validationTimeoutRef.current)
      }

      // Set new timeout for validation
      validationTimeoutRef.current = setTimeout(() => {
        validateYaml(text)
      }, 300) // 300ms delay to reduce validation frequency
    },
    [validateYaml]
  )

  // Handle text changes - only update buffer, not RHF
  const handleChange = React.useCallback(
    (newText: string) => {
      newText = stripNewline(newText)
      setBuffer(newText)
      // Use debounced validation to reduce re-renders during typing
      debouncedValidateYaml(newText)
    },
    [debouncedValidateYaml]
  )

  // Custom blur handler that commits YAML to form (stable function using refs)
  const yamlBlurHandler = useCallback(() => {
    return (event: FocusEvent, view: EditorView): boolean => {
      // Call the original blur handler for template pills
      const originalResult = createBlurHandler()(event, view)

      // Commit to form on blur if there are unsaved changes
      if (saveStateRef.current === SaveState.UNSAVED) {
        commitToFormRef.current()
        setSaveStateRef.current(SaveState.IDLE)
      }

      return originalResult
    }
  }, []) // No dependencies - stable function

  const extensions = useMemo(() => {
    const errorMonitorPlugin = ViewPlugin.fromClass(
      class {
        constructor(view: EditorView) {
          this.checkForErrors(view)
          editorRef.current = view
        }

        update(update: ViewUpdate) {
          if (update.docChanged) {
            this.checkForErrors(update.view)
          }
        }

        checkForErrors(view: EditorView) {
          try {
            const content = view.state.doc.toString()
            if (content.trim()) {
              YAML.parse(content)
            }
            setHasErrors(false)
          } catch (error) {
            setHasErrors(true)
          }
        }
      }
    )

    // Core navigation and editing keybindings (stable)
    const coreKeymap = keymap.of([
      {
        key: "ArrowLeft",
        run: enhancedCursorLeft,
      },
      {
        key: "ArrowRight",
        run: enhancedCursorRight,
      },
      {
        key: "Enter",
        run: (view: EditorView): boolean => {
          const currentEditingRange = view.state.field(editingRangeField)
          if (currentEditingRange) {
            // Clear editing state on Enter key
            view.dispatch({ effects: setEditingRange.of(null) })
            return true
          }
          return false
        },
      },
      ...closeBracketsKeymap,
      ...standardKeymap,
      ...historyKeymap,
      ...completionKeymap,
      indentWithTab,
    ])

    return [
      createPillDeleteKeymap(), // This must be first to ensure that the delete key is handled before the core keymap
      coreKeymap,
      createAtKeyCompletion(),
      createExitEditModeKeyHandler(),
      lintGutter(),
      history(),
      indentUnit.of("  "),
      yaml(),
      syntaxHighlighting(yamlSyntaxTheme),
      linter(customYamlLinter),

      bracketMatching(),
      closeBrackets(),
      autocompletion({
        override: [
          createMentionCompletion(),
          createFunctionCompletion(workspaceId),
          createActionCompletion(Object.values(actions).map((a) => a)),
          createSecretsCompletion(workspaceId),
          createEnvCompletion(),
          createVarCompletion(forEachExpressions),
        ],
      }),

      editingRangeField,
      createTemplatePillPlugin(workspaceId),
      createExpressionNodeHover(workspaceId),
      errorMonitorPlugin,

      EditorView.domEventHandlers({
        mousedown: createPillClickHandler(),
        blur: yamlBlurHandler(),
      }),

      templatePillTheme,
      yamlEditorTheme,
      yamlLiteralHighlighter,
    ]
  }, [workspaceId, actions, yamlBlurHandler]) // Only stable dependencies

  // Save-related keybindings (separate from core extensions to avoid recreation)
  const saveKeymap = useMemo(() => {
    return keymap.of([
      // Add Cmd+S / Ctrl+S for save
      {
        key: "Mod-s",
        run: () => {
          const success = commitToFormRef.current()
          if (success) {
            setSaveStateRef.current(SaveState.SAVED)
            setTimeout(() => setSaveStateRef.current(SaveState.IDLE), 2000)
          } else {
            setSaveStateRef.current(SaveState.ERROR)
          }
          return true
        },
        preventDefault: true,
      },
      // Add Cmd+Enter / Ctrl+Enter for explicit commit
      {
        key: "Mod-Enter",
        run: () => {
          const success = commitToFormRef.current()
          if (success) {
            setSaveStateRef.current(SaveState.SAVED)
            setTimeout(() => setSaveStateRef.current(SaveState.IDLE), 2000)
          } else {
            setSaveStateRef.current(SaveState.ERROR)
          }
          return true
        },
        preventDefault: true,
      },
    ])
  }, []) // Stable - uses refs

  // Combine all extensions
  const allExtensions = useMemo(() => {
    return [...extensions, saveKeymap]
  }, [extensions, saveKeymap])

  // Expose commitToForm method via ref
  React.useImperativeHandle(
    ref,
    () => ({
      commitToForm,
    }),
    [commitToForm]
  )

  return (
    <div className="relative">
      <div className="no-scrollbar max-h-[800px] overflow-auto rounded-md border-[0.5px] border-border shadow-sm">
        {fieldState.error && (
          <p className="mt-1 text-sm text-red-500">
            {fieldState.error.message ?? "Invalid YAML"}
          </p>
        )}
        <CodeMirror
          value={buffer}
          height="auto"
          extensions={allExtensions}
          onChange={handleChange}
          theme="light"
          basicSetup={{
            foldGutter: true,
            dropCursor: true,
            allowMultipleSelections: true,
            indentOnInput: true,
            lineNumbers: true,
            highlightActiveLineGutter: true,
            highlightSpecialChars: true,
            history: true,
            drawSelection: true,
            syntaxHighlighting: true,
            autocompletion: true,
            bracketMatching: true,
            closeBrackets: true,
            highlightActiveLine: true,
            rectangularSelection: true,
            lintKeymap: true,
            defaultKeymap: false,
          }}
          className={EDITOR_STYLE}
        />
      </div>

      <div className="absolute bottom-2 right-2 z-10 flex items-center gap-2">
        {/* Save state indicator */}
        {saveState === SaveState.UNSAVED && (
          <div className="flex items-center gap-1 rounded-md bg-yellow-100 px-2 py-1 text-xs text-yellow-800">
            <span>Unsaved</span>
            <span className="text-[10px] text-yellow-600">
              {typeof navigator !== "undefined" &&
              /Mac|iPod|iPhone|iPad/.test(navigator.userAgent)
                ? "⌘"
                : "Ctrl"}
              +S
            </span>
          </div>
        )}
        {saveState === SaveState.SAVED && (
          <div className="flex items-center gap-1 rounded-md bg-green-100 px-2 py-1 text-xs text-green-800">
            <Check className="size-3" />
            <span>Saved</span>
          </div>
        )}
        {saveState === SaveState.ERROR && (
          <div
            className="flex items-center gap-1 rounded-md bg-destructive/10 px-2 py-1 text-xs text-destructive"
            title={validationErrors.join("\n")}
          >
            <AlertTriangle className="size-3" />
            <span>Error</span>
          </div>
        )}
        {/* Existing syntax error indicator */}
        {hasErrors && saveState !== SaveState.ERROR && (
          <div
            className="flex items-center gap-1 rounded-md bg-destructive/10 px-2 py-1 text-xs text-destructive"
            title="YAML syntax error"
          >
            <AlertTriangle className="size-3" />
            <span>Syntax error</span>
          </div>
        )}
      </div>
    </div>
  )
})

YamlStyledEditor.displayName = "YamlStyledEditor"

// Custom YAML linter with enhanced error reporting
function customYamlLinter(view: EditorView): Diagnostic[] {
  const diagnostics: Diagnostic[] = []
  const content = view.state.doc.toString()

  if (!content.trim()) {
    return diagnostics
  }

  try {
    YAML.parse(content)
  } catch (error) {
    if (!(error instanceof Error)) {
      return []
    }
    let from = 0
    let to = content.length
    let message = "Invalid YAML"

    if (error.message) {
      message = error.message

      // YAML errors often include line and column information
      const lineMatch = error.message.match(/line (\d+), column (\d+)/)
      if (lineMatch) {
        const line = parseInt(lineMatch[1])
        const column = parseInt(lineMatch[2]) - 1
        try {
          const lineStart = view.state.doc.line(line).from
          from = lineStart + column
          to = Math.min(content.length, from + 1)
        } catch {
          // Fallback if line/column parsing fails
          from = 0
          to = Math.min(content.length, 10)
        }
      } else {
        // Try to extract position from other YAML error formats
        const positionMatch = error.message.match(/at position (\d+)/)
        if (positionMatch) {
          const position = parseInt(positionMatch[1])
          from = Math.max(0, position - 1)
          to = Math.min(content.length, position + 1)
        }
      }
    }

    diagnostics.push({
      from,
      to,
      severity: "error",
      message,
      source: "yaml",
    })
  }

  return diagnostics
}

const yamlSyntaxTheme = HighlightStyle.define([
  { tag: tags.content, color: "#586069" },
  { tag: tags.propertyName, color: "#0077aa", fontWeight: "500" },
  { tag: tags.string, color: "#10b981" },
  { tag: tags.number, color: "#005cc5" },
  { tag: tags.bool, color: "#e36209" },
  { tag: tags.atom, color: "#e36209", fontWeight: "600" },
  { tag: tags.keyword, color: "#e36209" },
  { tag: tags.comment, color: "#6a737d", fontStyle: "italic" },
  { tag: [tags.punctuation, tags.bracket, tags.brace], color: "#586069" },
])
const yamlEditorTheme = EditorView.theme({
  ".cm-content": {
    whiteSpace: "pre !important",
    overflowX: "auto",
  },
  ".cm-line": {
    whiteSpace: "pre !important",
  },
  ".cm-scroller": {
    overflowX: "auto",
  },
  ".cm-diagnostic-error": {
    borderBottom: "2px wavy #ef4444",
  },
  ".cm-diagnostic.cm-diagnostic-error": {
    backgroundColor: "rgba(239, 68, 68, 0.1)",
    borderRadius: "2px",
  },
  ".cm-lint-marker-error": {
    backgroundColor: "#ef4444",
    borderRadius: "50%",
    width: "0.8em",
    height: "0.8em",
  },
  ".cm-tooltip.cm-tooltip-lint": {
    backgroundColor: "#1f2937",
    color: "#f9fafb",
    border: "1px solid #374151",
    borderRadius: "6px",
    padding: "8px 12px",
    fontSize: "12px",
    maxWidth: "300px",
    boxShadow: "0 4px 6px -1px rgba(0, 0, 0, 0.1)",
  },
  ".cm-tooltip-lint .cm-diagnostic-error": {
    color: "#fca5a5",
  },
  ".cm-template-expression-tooltip": {
    backgroundColor: "#1f2937",
    color: "#f9fafb",
    border: "1px solid #374151",
    borderRadius: "8px",
    padding: "12px",
    fontSize: "12px",
    maxWidth: "400px",
    boxShadow:
      "0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -2px rgba(0, 0, 0, 0.05)",
  },
  ".cm-tooltip-header": {
    fontWeight: "600",
    marginBottom: "8px",
    color: "#e5e7eb",
    borderBottom: "1px solid #374151",
    paddingBottom: "4px",
  },
  ".cm-tooltip-status": {
    marginBottom: "8px",
    padding: "4px 8px",
    borderRadius: "4px",
    fontSize: "11px",
    fontWeight: "500",
  },
  ".cm-tooltip-status.valid": {
    backgroundColor: "rgba(34, 197, 94, 0.2)",
    color: "#86efac",
  },
  ".cm-tooltip-status.invalid": {
    backgroundColor: "rgba(239, 68, 68, 0.2)",
    color: "#fca5a5",
  },
  ".cm-tooltip-section-title": {
    fontWeight: "500",
    marginBottom: "4px",
    color: "#d1d5db",
  },
  ".cm-tooltip-errors": {
    marginBottom: "8px",
  },
  ".cm-tooltip-error-item": {
    color: "#fca5a5",
    fontSize: "11px",
    marginBottom: "2px",
  },
  ".cm-tooltip-tokens": {
    marginBottom: "4px",
  },
  ".cm-tooltip-tokens-list": {
    display: "flex",
    flexWrap: "wrap",
    gap: "4px",
  },
  ".cm-tooltip-token": {
    padding: "2px 6px",
    borderRadius: "3px",
    fontSize: "10px",
    fontFamily: "monospace",
    backgroundColor: "rgba(55, 65, 81, 0.5)",
    border: "1px solid #4b5563",
  },
  ".function-completion-info": {
    backgroundColor: "#1f2937",
    color: "#f9fafb",
    border: "1px solid #374151",
    borderRadius: "8px",
    padding: "12px",
    fontSize: "12px",
    maxWidth: "400px",
    boxShadow:
      "0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -2px rgba(0, 0, 0, 0.05)",
  },
  ".function-signature": {
    fontFamily: "monospace",
    fontWeight: "600",
    marginBottom: "8px",
    color: "#e5e7eb",
    borderBottom: "1px solid #374151",
    paddingBottom: "4px",
  },
  ".function-description": {
    marginBottom: "8px",
    color: "#d1d5db",
    fontSize: "11px",
  },
  ".function-params-title": {
    fontWeight: "500",
    marginBottom: "4px",
    color: "#d1d5db",
  },
  ".function-param": {
    color: "#d1d5db",
    fontSize: "11px",
    marginBottom: "2px",
    paddingLeft: "8px",
  },
  ".action-completion-info": {
    backgroundColor: "#1f2937",
    color: "#f9fafb",
    border: "1px solid #374151",
    borderRadius: "8px",
    padding: "12px",
    fontSize: "12px",
    maxWidth: "400px",
    boxShadow:
      "0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -2px rgba(0, 0, 0, 0.05)",
  },
  ".action-signature": {
    fontFamily: "monospace",
    fontWeight: "600",
    marginBottom: "8px",
    color: "#e5e7eb",
    borderBottom: "1px solid #374151",
    paddingBottom: "4px",
  },
  ".action-description": {
    marginBottom: "8px",
    color: "#d1d5db",
    fontSize: "11px",
  },
  ".action-props-title": {
    fontWeight: "500",
    marginBottom: "4px",
    color: "#d1d5db",
  },
  ".action-prop": {
    color: "#d1d5db",
    fontSize: "11px",
    marginBottom: "2px",
    paddingLeft: "8px",
  },
})

// Custom semantic highlighting for true/false/null literals
const yamlLiteralHighlighter = ViewPlugin.fromClass(
  class {
    decorations: DecorationSet

    constructor(view: EditorView) {
      this.decorations = this.buildDecorations(view)
    }

    update(update: ViewUpdate) {
      if (update.docChanged || update.viewportChanged) {
        this.decorations = this.buildDecorations(update.view)
      }
    }

    buildDecorations(view: EditorView) {
      const decorations: Array<{
        from: number
        to: number
        value: Decoration
      }> = []

      // Define decoration styles
      const booleanMark = Decoration.mark({
        class: "cm-yaml-boolean",
        attributes: {
          style: "font-weight: 700 !important;",
        },
      })
      const nullMark = Decoration.mark({
        class: "cm-yaml-null",
        attributes: {
          style: "font-weight: 700 !important;",
        },
      })

      const doc = view.state.doc
      const text = doc.toString()

      // Regular expressions to match unquoted literals
      // Match true/false/null that are:
      // - At start of line after whitespace and colon
      // - In arrays after dash and whitespace
      // - Not inside quotes
      const patterns = [
        // Value after key: true/false/null
        /^(\s*)([\w-]+)(\s*:\s*)(true|false|null)(\s*(?:#.*)?$)/gm,
        // Array item: - true/false/null
        /^(\s*-)(\s+)(true|false|null)(\s*(?:#.*)?$)/gm,
        // Flow sequence: [true, false, null]
        /([,\[])\s*(true|false|null)\s*([,\]])/g,
        // Flow mapping: {key: true}
        /(:)\s*(true|false|null)\s*([,}])/g,
      ]

      for (const pattern of patterns) {
        let match
        while ((match = pattern.exec(text)) !== null) {
          const valueMatch = match[0].match(/(true|false|null)/)
          if (valueMatch) {
            const value = valueMatch[0]
            const valueIndex = match.index + match[0].indexOf(value)
            const from = valueIndex
            const to = valueIndex + value.length

            // Additional check: ensure we're not in a comment
            const line = doc.lineAt(from)
            const lineText = line.text
            const commentIndex = lineText.indexOf("#")
            const posInLine = from - line.from

            if (commentIndex === -1 || posInLine < commentIndex) {
              if (value === "true" || value === "false") {
                decorations.push({ from, to, value: booleanMark })
              } else if (value === "null") {
                decorations.push({ from, to, value: nullMark })
              }
            }
          }
        }
      }

      return Decoration.set(
        decorations.sort((a, b) => a.from - b.from),
        true
      )
    }
  },
  {
    decorations: (v) => v.decorations,
  }
)

export function YamlViewOnlyEditor({
  value,
  className,
}: {
  value: unknown
  className?: string
}) {
  const { workspaceId } = useWorkspace()

  const textValue = React.useMemo(() => {
    if (!value) return ""
    return stripNewline(
      typeof value === "string" ? value : YAML.stringify(value)
    )
  }, [value])

  const extensions = useMemo(() => {
    return [
      // Core language support with proper indentation
      indentUnit.of("  "),
      yaml(),
      syntaxHighlighting(yamlSyntaxTheme),
      bracketMatching(),

      // Read-only configuration
      EditorView.editable.of(false),
      EditorState.readOnly.of(true),

      // Visual features - keep pills and hover
      editingRangeField,
      createTemplatePillPlugin(workspaceId),

      // Styling
      templatePillTheme,
      yamlEditorTheme,
      yamlLiteralHighlighter,
    ]
  }, [workspaceId])

  return (
    <div className="relative">
      <div
        className={cn(
          "no-scrollbar overflow-auto rounded-md border-[0.5px] border-border bg-muted/50 shadow-sm",
          className
        )}
      >
        <CodeMirror
          value={textValue}
          height="auto"
          extensions={extensions}
          theme="light"
          editable={false}
          basicSetup={{
            foldGutter: true,
            lineNumbers: true,
            highlightActiveLineGutter: false,
            highlightSpecialChars: true,
            drawSelection: false,
            syntaxHighlighting: true,
            bracketMatching: true,
            highlightActiveLine: false,
            rectangularSelection: false,
            defaultKeymap: false,
            autocompletion: false,
            closeBrackets: false,
            dropCursor: false,
            allowMultipleSelections: false,
            indentOnInput: false,
            history: false,
            lintKeymap: false,
          }}
          className={EDITOR_STYLE}
        />
      </div>
    </div>
  )
}

YamlViewOnlyEditor.displayName = "YamlViewOnlyEditor"
