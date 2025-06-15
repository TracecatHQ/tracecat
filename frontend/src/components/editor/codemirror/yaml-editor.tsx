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
import { bracketMatching, indentUnit } from "@codemirror/language"
import { linter, lintGutter, type Diagnostic } from "@codemirror/lint"
import {
  EditorView,
  keymap,
  ViewPlugin,
  type ViewUpdate,
} from "@codemirror/view"
import CodeMirror from "@uiw/react-codemirror"
import { AlertTriangle, Check, Code } from "lucide-react"
import { Control, FieldValues, useController } from "react-hook-form"
import YAML from "yaml"

import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"

import {
  createActionCompletion,
  createAtKeyCompletion,
  createBlurHandler,
  createEscapeKeyHandler,
  createExpressionNodeHover,
  createFunctionCompletion,
  createMentionCompletion,
  createPillClickHandler,
  createTemplatePillPlugin,
  editingRangeField,
  enhancedCursorLeft,
  enhancedCursorRight,
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
  }
>(({ name, control }, ref) => {
  const { field, fieldState } = useController<FieldValues>({
    name: name,
    control,
  })
  const { workspaceId } = useWorkspace()
  const { workflowId, workflow } = useWorkflow()
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

  // Commit valid YAML to RHF (only when explicitly triggered)
  const commitToForm = useCallback(() => {
    try {
      const obj = YAML.parse(buffer)
      field.onChange(obj) // Push valid object to RHF
      setValidationErrors([])
      setHasErrors(false)
      return true
    } catch (err) {
      // Invalid YAML – don't update RHF, keep last valid value
      console.warn("YAML parse error on commit:", err)
      setValidationErrors([err instanceof Error ? err.message : "Invalid YAML"])
      return false
    }
  }, [buffer, field])

  // Save function that commits to RHF and updates save state
  const handleSave = useCallback(() => {
    const success = commitToForm()
    if (success) {
      setSaveState(SaveState.SAVED)
      // Reset to idle after brief delay
      setTimeout(() => setSaveState(SaveState.IDLE), 2000)
    } else {
      setSaveState(SaveState.ERROR)
    }
  }, [commitToForm])

  // Debounced validation for visual feedback
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

  // Handle text changes - only update buffer, not RHF
  const handleChange = React.useCallback(
    (newText: string) => {
      newText = stripNewline(newText)
      setBuffer(newText)
      // Validate for visual feedback only - no longer push to RHF here
      validateYaml(newText)
    },
    [validateYaml]
  )
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

    // Custom blur handler that commits YAML to form
    const yamlBlurHandler = () => {
      return (event: FocusEvent, view: EditorView): boolean => {
        // Call the original blur handler for template pills
        const originalResult = createBlurHandler()(event, view)

        // Commit to form on blur if there are unsaved changes
        if (saveState === SaveState.UNSAVED) {
          commitToForm()
          setSaveState(SaveState.IDLE)
        }

        return originalResult
      }
    }

    return [
      keymap.of([
        {
          key: "ArrowLeft",
          run: enhancedCursorLeft,
        },
        {
          key: "ArrowRight",
          run: enhancedCursorRight,
        },
        // Add Cmd+S / Ctrl+S for save
        {
          key: "Mod-s",
          run: () => {
            handleSave()
            return true
          },
          preventDefault: true,
        },
        // Add Cmd+Enter / Ctrl+Enter for explicit commit
        {
          key: "Mod-Enter",
          run: () => {
            const success = commitToForm()
            if (success) {
              setSaveState(SaveState.SAVED)
              setTimeout(() => setSaveState(SaveState.IDLE), 2000)
            } else {
              setSaveState(SaveState.ERROR)
            }
            return true
          },
          preventDefault: true,
        },
        ...closeBracketsKeymap,
        ...standardKeymap,
        ...historyKeymap,
        ...completionKeymap,
        indentWithTab,
      ]),
      createAtKeyCompletion(),
      createEscapeKeyHandler(),
      lintGutter(),
      history(),
      indentUnit.of("  "),
      EditorView.lineWrapping,
      yaml(),
      linter(customYamlLinter),

      bracketMatching(),
      closeBrackets(),
      autocompletion({
        override: [
          createMentionCompletion(),
          createFunctionCompletion(workspaceId),
          createActionCompletion(Object.values(actions).map((a) => a)),
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
    ]
  }, [workspaceId, workflowId, handleSave, saveState, commitToForm])

  // Expose commitToForm method via ref
  React.useImperativeHandle(ref, () => ({
    commitToForm,
  }), [commitToForm])

  return (
    <div className="relative">
      <div className="no-scrollbar max-h-[800px] overflow-auto rounded-md border shadow-sm">
        {fieldState.error && (
          <p className="mt-1 text-sm text-red-500">
            {fieldState.error.message ?? "Invalid YAML"}
          </p>
        )}
        <CodeMirror
          value={buffer}
          height="auto"
          extensions={extensions}
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
          className={cn(
            "rounded-md text-xs focus-visible:outline-none",
            "[&_.cm-editor]:rounded-md [&_.cm-editor]:border-0 [&_.cm-focused]:outline-none",
            "[&_.cm-scroller]:rounded-md",
            "[&_.cm-tooltip]:rounded-md",
            "[&_.cm-tooltip-autocomplete]:rounded-sm [&_.cm-tooltip-autocomplete]:p-0.5",
            "[&_.cm-tooltip-autocomplete>ul]:rounded-sm",
            "[&_.cm-tooltip-autocomplete>ul>li]:flex",
            "[&_.cm-tooltip-autocomplete>ul>li]:min-h-5",
            "[&_.cm-tooltip-autocomplete>ul>li]:items-center",
            "[&_.cm-tooltip-autocomplete>ul>li]:rounded-sm",
            "[&_.cm-tooltip-autocomplete>ul>li[aria-selected=true]]:bg-sky-200/50",
            "[&_.cm-tooltip-autocomplete>ul>li[aria-selected=true]]:text-accent-foreground",
            "[&_.cm-tooltip-autocomplete>ul>li]:py-2.5"
          )}
        />
      </div>

      <div className="absolute bottom-2 right-2 z-10 flex items-center gap-2">
        {/* Save state indicator */}
        {saveState === SaveState.UNSAVED && (
          <div className="flex items-center gap-1 rounded-md bg-yellow-100 px-2 py-1 text-xs text-yellow-800">
            <span>Unsaved</span>
            <span className="text-[10px] text-yellow-600">
              {typeof navigator !== "undefined" && /Mac|iPod|iPhone|iPad/.test(navigator.userAgent) ? "⌘" : "Ctrl"}+S
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
        <Button
          size="sm"
          variant="secondary"
          onClick={() => {
            console.log("formatYaml")
          }}
          className="h-8 px-2 shadow-md transition-shadow hover:shadow-lg"
          title="Format YAML"
          disabled={hasErrors}
        >
          <Code className="mr-1 size-3" />
          Format
        </Button>
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

const yamlEditorTheme = EditorView.theme({
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
