"use client"

import React, { useMemo, useState } from "react"
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
import { AlertTriangle, Code } from "lucide-react"
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
  setEditingRange,
  templatePillTheme,
} from "./common"

const stripNewline = (value: string) => {
  return value.endsWith("\n") ? value.slice(0, -1) : value
}
export function YamlStyledEditor({
  name,
  control,
}: {
  name: string
  control: Control<FieldValues>
}) {
  const { field, fieldState } = useController<FieldValues>({
    name: name,
    control,
  })
  const { workspaceId } = useWorkspace()
  const { workflowId, workflow } = useWorkflow()
  const [editorView, setEditorView] = useState<EditorView | null>(null)
  const [hasErrors, setHasErrors] = useState(false)
  const actions = workflow?.actions || []

  const textValue = React.useMemo(
    () => stripNewline(field.value ? YAML.stringify(field.value) : ""),
    [field.value]
  )
  // Internal editor value - always a string representation of the YAML
  const [buffer, setBuffer] = useState(textValue)
  console.log("buffer", JSON.stringify(buffer), buffer.endsWith("\n"))

  // Sync external changes into the buffer
  React.useEffect(() => setBuffer(textValue), [textValue])

  const handleChange = React.useCallback(
    (newText: string) => {
      newText = stripNewline(newText)
      setBuffer(newText)

      try {
        const obj = YAML.parse(newText)
        field.onChange(obj) // ✅ push valid object to RHF
      } catch (err) {
        // Invalid JSON – keep RHF value unchanged and surface an error
        console.warn("error", err)
        field.onChange(field.value) // noop
      }
    },
    [field]
  )
  const extensions = useMemo(() => {
    const errorMonitorPlugin = ViewPlugin.fromClass(
      class {
        constructor(view: EditorView) {
          this.checkForErrors(view)
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
        blur: createBlurHandler(),
      }),

      templatePillTheme,
      yamlEditorTheme,
    ]
  }, [workspaceId, workflowId])

  return (
    <div className="relative">
      <div className="no-scrollbar max-h-[800px] overflow-auto rounded-md border shadow-sm">
        {fieldState.error && (
          <p className="mt-1 text-sm text-red-500">
            {fieldState.error.message ?? "Invalid JSON"}
          </p>
        )}
        <CodeMirror
          value={buffer}
          height="auto"
          extensions={extensions}
          onChange={handleChange}
          theme="light"
          onCreateEditor={(view) => setEditorView(view)}
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
        {hasErrors && (
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
}

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
