"use client"

/**
 * Enhanced JSON Editor with Template Expression Pills
 *
 * Keyboard Navigation for Template Pills:
 * - Click on a pill to enter editing mode
 * - Arrow Left/Right: Smart cursor movement - enters pills when at boundaries
 */
import React, { useMemo, useState } from "react"
import { EditorActionRead, EditorFunctionRead } from "@/client"
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
import { json, jsonParseLinter } from "@codemirror/lang-json"
import { bracketMatching, indentUnit } from "@codemirror/language"
import { linter, lintGutter, type Diagnostic } from "@codemirror/lint"
import { EditorState } from "@codemirror/state"
import {
  EditorView,
  keymap,
  ViewPlugin,
  type ViewUpdate,
} from "@codemirror/view"
import CodeMirror from "@uiw/react-codemirror"
import { AlertTriangle, Code } from "lucide-react"
import { createRoot } from "react-dom/client"

import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"

import {
  actionCache,
  createActionCompletion,
  createExpressionNodeHover,
  createFunctionCompletion,
  createMentionCompletion,
  createPillClickHandler,
  createTemplatePillPlugin,
  editingRangeField,
  enhancedCursorLeft,
  enhancedCursorRight,
  functionCache,
  TemplateExpressionValidation,
  templatePillTheme,
} from "./codemirror/common"

// JSX Components for tooltip content
interface FunctionTooltipProps {
  func: EditorFunctionRead
}

function FunctionTooltip({ func }: FunctionTooltipProps) {
  const parameterList = func.parameters
    .map((p) => `${p.name}: ${p.type}`)
    .join(", ")

  return (
    <div className="function-completion-info">
      <div className="function-signature">
        {func.name}({parameterList}) → {func.return_type}
      </div>
      {func.description && (
        <div className="function-description">{func.description}</div>
      )}
      {func.parameters && func.parameters.length > 0 && (
        <>
          <div className="function-params-title">Parameters:</div>
          {func.parameters.map((param, index) => (
            <div key={index} className="function-param">
              <strong>{param.name}</strong>
              {param.optional && " (optional)"}: <em>{param.type}</em>
            </div>
          ))}
        </>
      )}
    </div>
  )
}

interface ActionTooltipProps {
  action: EditorActionRead
}

function ActionTooltip({ action }: ActionTooltipProps) {
  const commonProps = ["result", "error", "status"]

  return (
    <div className="action-completion-info">
      <div className="action-signature">
        {action.ref} ({action.type})
      </div>
      {action.description && (
        <div className="action-description">{action.description}</div>
      )}
      <div className="action-props-title">Available properties:</div>
      {commonProps.map((prop, index) => (
        <div key={index} className="action-prop">
          <strong>{prop}</strong>: Access {prop} from this action
        </div>
      ))}
    </div>
  )
}

interface TemplateTooltipProps {
  innerContent: string
  validation: TemplateExpressionValidation
  func?: EditorFunctionRead
  action?: EditorActionRead
}

function TemplateTooltip({
  innerContent,
  validation,
  func,
  action,
}: TemplateTooltipProps) {
  return (
    <div className="cm-template-expression-tooltip">
      <div className="cm-tooltip-header">
        Template Expression: {innerContent}
      </div>

      {func && <FunctionTooltip func={func} />}
      {action && <ActionTooltip action={action} />}

      <div
        className={`cm-tooltip-status ${validation.isValid ? "valid" : "invalid"}`}
      >
        {validation.isValid ? "✓ Valid" : "✗ Invalid"}
      </div>

      {validation.errors && validation.errors.length > 0 && (
        <div className="cm-tooltip-errors">
          <div className="cm-tooltip-section-title">Errors:</div>
          {validation.errors.map((error, index) => (
            <div key={index} className="cm-tooltip-error-item">
              • {error.msg}
            </div>
          ))}
        </div>
      )}

      {validation.tokens && validation.tokens.length > 0 && (
        <div className="cm-tooltip-tokens">
          <div className="cm-tooltip-section-title">Tokens:</div>
          <div className="cm-tooltip-tokens-list">
            {validation.tokens.map((token, index) => (
              <span
                key={index}
                className={`cm-tooltip-token cm-token-${token.type.toLowerCase()}`}
                title={`Type: ${token.type}`}
              >
                {token.value}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// Helper function to render JSX to DOM element for CodeMirror tooltips
function renderTooltipJSX(component: React.ReactElement): HTMLElement {
  try {
    const container = document.createElement("div")
    const root = createRoot(container)
    root.render(component)
    return container
  } catch (error) {
    throw error
  }
}

// Custom JSON linter with enhanced error reporting
function customJsonLinter(view: EditorView): Diagnostic[] {
  const diagnostics: Diagnostic[] = []
  const content = view.state.doc.toString()

  if (!content.trim()) {
    return diagnostics
  }

  try {
    JSON.parse(content)
  } catch (error) {
    if (!(error instanceof Error)) {
      return []
    }
    let from = 0
    let to = content.length
    let message = "Invalid JSON"

    if (error.message) {
      message = error.message
      const positionMatch = error.message.match(/at position (\d+)/)
      if (positionMatch) {
        const position = parseInt(positionMatch[1])
        from = Math.max(0, position - 1)
        to = Math.min(content.length, position + 1)
      }

      const lineMatch = error.message.match(/line (\d+) column (\d+)/)
      if (lineMatch) {
        const line = parseInt(lineMatch[1]) - 1
        const column = parseInt(lineMatch[2]) - 1
        const lineStart = view.state.doc.line(line + 1).from
        from = lineStart + column
        to = Math.min(content.length, from + 1)
      }
    }

    diagnostics.push({
      from,
      to,
      severity: "error",
      message,
      source: "json",
    })
  }

  return diagnostics
}

// Helper function to create tooltip content with function/action information using JSX
function createTooltipContentJSX(
  info: { validation: TemplateExpressionValidation; innerContent: string },
  workspaceId: string,
  workflowId: string | null
): React.ReactElement {
  let func: EditorFunctionRead | undefined
  let action: EditorActionRead | undefined

  const fnMatch = info.innerContent.match(/^FN\.(\w+)/)
  if (fnMatch) {
    const functionName = fnMatch[1]
    const cachedFunctions = functionCache.get(workspaceId)
    if (cachedFunctions) {
      func = cachedFunctions.find((f) => f.name === functionName)
    }
  }

  const actionMatch = info.innerContent.match(/^ACTIONS\.(\w+)/)
  if (actionMatch && workflowId) {
    const actionRef = actionMatch[1]
    const cacheKey = `${workspaceId}-${workflowId}`
    const cachedActions = actionCache.get(cacheKey)
    if (cachedActions) {
      action = cachedActions.find((a) => a.ref === actionRef)
    }
  }

  return (
    <TemplateTooltip
      innerContent={info.innerContent}
      validation={info.validation}
      func={func}
      action={action}
    />
  )
}

export function JsonStyledEditor({
  value,
  setValue,
}: {
  value: string
  setValue: (value: string) => void
}) {
  const { workspaceId } = useWorkspace()
  const { workflowId, workflow } = useWorkflow()
  const [editorView, setEditorView] = useState<EditorView | null>(null)
  const [hasErrors, setHasErrors] = useState(false)
  const actions = workflow?.actions || []

  const editorValue =
    typeof value === "string"
      ? value
      : value
        ? JSON.stringify(value, null, 2)
        : ""

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
              JSON.parse(content)
            }
            setHasErrors(false)
          } catch (error) {
            setHasErrors(true)
          }
        }
      }
    )

    const editablePillPluginInstance = createTemplatePillPlugin(workspaceId)
    return [
      lintGutter(),
      history(),
      EditorState.allowMultipleSelections.of(true),
      indentUnit.of("  "),
      EditorView.lineWrapping,

      json(),
      linter(jsonParseLinter()),
      linter(customJsonLinter),

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

      bracketMatching(),
      closeBrackets(),
      autocompletion({
        override: [
          createMentionCompletion(),
          createFunctionCompletion(workspaceId),
          ...(workflowId
            ? [createActionCompletion(Object.values(actions).map((a) => a))]
            : []),
        ],
      }),

      editingRangeField,
      editablePillPluginInstance,
      createExpressionNodeHover(workspaceId),
      errorMonitorPlugin,

      EditorView.domEventHandlers({
        mousedown: createPillClickHandler(),
      }),

      templatePillTheme,

      EditorView.theme({
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
      }),
    ]
  }, [workspaceId, workflowId])

  const editorTheme = "light"

  const onChange = React.useCallback(
    (val: string, viewUpdate: ViewUpdate) => {
      setValue(val)
    },
    [setValue]
  )

  const formatJson = React.useCallback(() => {
    if (!editorView) return

    try {
      const currentValue = editorView.state.doc.toString()
      const parsed = JSON.parse(currentValue)
      const formatted = JSON.stringify(parsed, null, 2)

      editorView.dispatch({
        changes: {
          from: 0,
          to: editorView.state.doc.length,
          insert: formatted,
        },
      })
    } catch (error) {
      console.warn("Cannot format invalid JSON:", error)
    }
  }, [editorView])

  return (
    <div className="relative">
      <div className="no-scrollbar max-h-[800px] overflow-auto rounded-md border">
        <CodeMirror
          value={editorValue}
          height="auto"
          extensions={extensions}
          onChange={onChange}
          theme={editorTheme}
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
            title="JSON syntax error"
          >
            <AlertTriangle className="size-3" />
            <span>Syntax error</span>
          </div>
        )}
        <Button
          size="sm"
          variant="secondary"
          onClick={formatJson}
          className="h-8 px-2 shadow-md transition-shadow hover:shadow-lg"
          title="Format JSON"
          disabled={hasErrors}
        >
          <Code className="mr-1 size-3" />
          Format
        </Button>
      </div>
    </div>
  )
}
