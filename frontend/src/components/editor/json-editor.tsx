"use client"

/**
 * Enhanced JSON Editor with Template Expression Pills
 *
 * Keyboard Navigation for Template Pills:
 * - Click on a pill to enter editing mode
 * - Arrow Left/Right: Smart cursor movement - enters pills when at boundaries
 */
import React, { useMemo, useState } from "react"
import {
  ActionRead,
  EditorActionRead,
  EditorFunctionRead,
  editorListFunctions,
  editorValidateExpression,
  ExpressionValidationResponse,
} from "@/client"
import { useWorkflow } from "@/providers/workflow"
import { useWorkspace } from "@/providers/workspace"
import {
  autocompletion,
  closeBrackets,
  closeBracketsKeymap,
  CompletionContext,
  completionKeymap,
  CompletionResult,
} from "@codemirror/autocomplete"
import {
  cursorCharLeft,
  cursorCharRight,
  history,
  historyKeymap,
  indentWithTab,
  standardKeymap,
} from "@codemirror/commands"
import { json, jsonParseLinter } from "@codemirror/lang-json"
import { bracketMatching, indentUnit, syntaxTree } from "@codemirror/language"
import { linter, lintGutter, type Diagnostic } from "@codemirror/lint"
import {
  EditorState,
  StateEffect,
  StateField,
  type Range,
} from "@codemirror/state"
import {
  Decoration,
  DecorationSet,
  EditorView,
  hoverTooltip,
  keymap,
  ViewPlugin,
  WidgetType,
  type ViewUpdate,
} from "@codemirror/view"
import type { SyntaxNode } from "@lezer/common"
import CodeMirror from "@uiw/react-codemirror"
import { AlertTriangle, Code } from "lucide-react"
import { useTheme } from "next-themes"
import { createRoot } from "react-dom/client"

import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"

// Replace global TEMPLATE_REGEX with a function to create a new instance
function createTemplateRegex() {
  return /\$\{\{\s*(.*?)\s*\}\}/g
}

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
// LSP client for template expression validation and highlighting
interface TemplateExpressionValidation {
  isValid: boolean
  errors?: Array<{
    loc: Array<string | number>
    msg: string
    type: string
  }>
  tokens?: Array<{
    type: string
    value: string
    start: number
    end: number
  }>
}
const editablePillPlugin = (workspaceId: string) => {
  return ViewPlugin.fromClass(
    class {
      decorations: DecorationSet
      validationCache = new Map<string, TemplateExpressionValidation>()
      pendingValidations = new Map<
        string,
        Promise<TemplateExpressionValidation>
      >()

      constructor(view: EditorView) {
        this.decorations = this.buildDecorations(view)
      }

      update(update: ViewUpdate) {
        if (
          update.docChanged ||
          update.viewportChanged ||
          update.state.field(editingRangeField) !==
            update.startState.field(editingRangeField) ||
          update.transactions.some((tr) => tr.effects.length > 0)
        ) {
          this.decorations = this.buildDecorations(update.view)
        }
      }

      async getValidation(
        expression: string
      ): Promise<TemplateExpressionValidation | undefined> {
        // Check cache first
        if (this.validationCache.has(expression)) {
          return this.validationCache.get(expression)
        }

        // Check if validation is already pending
        if (this.pendingValidations.has(expression)) {
          return await this.pendingValidations.get(expression)
        }

        // Start new validation
        const validationPromise = validateTemplateExpression(
          expression,
          workspaceId
        )
        this.pendingValidations.set(expression, validationPromise)

        try {
          const result = await validationPromise
          this.validationCache.set(expression, result)
          this.pendingValidations.delete(expression)
          return result
        } catch (error) {
          this.pendingValidations.delete(expression)
          return undefined
        }
      }

      // Get validation for a specific position (used by hover tooltip)
      getValidationAt(
        pos: number,
        view: EditorView
      ): TemplateExpressionValidation | null {
        let result: TemplateExpressionValidation | null = null

        for (const { from, to } of view.visibleRanges) {
          syntaxTree(view.state).iterate({
            from,
            to,
            enter: (node: SyntaxNode) => {
              if (node.type.name === "String") {
                const stringValue = view.state.doc.sliceString(
                  node.from + 1,
                  node.to - 1
                )
                const regex = createTemplateRegex()
                let match: RegExpExecArray | null
                while ((match = regex.exec(stringValue)) !== null) {
                  const innerContent = match[1].trim()
                  const start = node.from + 1 + match.index
                  const end = start + match[0].length

                  // Check if position is within this template expression
                  if (pos >= start && pos <= end) {
                    result = this.validationCache.get(innerContent) || null
                    return false // Stop iteration
                  }
                }
              }
            },
          })
          if (result) break
        }
        return result
      }

      buildDecorations(view: EditorView): DecorationSet {
        const widgets: Range<Decoration>[] = []
        const editingRange = view.state.field(editingRangeField)

        for (const { from, to } of view.visibleRanges) {
          syntaxTree(view.state).iterate({
            from,
            to,
            enter: (node: SyntaxNode) => {
              if (node.type.name === "String") {
                const stringValue = view.state.doc.sliceString(
                  node.from + 1,
                  node.to - 1
                )
                const regex = createTemplateRegex()
                let match: RegExpExecArray | null
                while ((match = regex.exec(stringValue)) !== null) {
                  const fullMatchText = match[0]
                  const innerContent = match[1].trim()
                  const start = node.from + 1 + match.index
                  const end = start + fullMatchText.length

                  // Skip if this range is currently being edited
                  if (
                    editingRange &&
                    editingRange.from === start &&
                    editingRange.to === end
                  ) {
                    continue
                  }

                  // Get cached validation or create widget without validation
                  const validation = this.validationCache.get(innerContent)

                  // Create immediate validation for obvious errors (for testing)
                  let immediateValidation = validation
                  if (!validation && innerContent) {
                    // Simple client-side validation for obvious errors
                    if (
                      innerContent.includes("..") ||
                      innerContent.endsWith(".") ||
                      innerContent.includes("undefined")
                    ) {
                      immediateValidation = {
                        isValid: false,
                        errors: [
                          {
                            loc: [0],
                            msg: "Invalid expression syntax",
                            type: "syntax_error",
                          },
                        ],
                        tokens: [],
                      }
                    }
                  }

                  const widget = new InnerContentWidget(
                    innerContent,
                    immediateValidation
                  )

                  // Trigger async validation if not cached
                  if (!validation && innerContent) {
                    // Only validate non-empty expressions
                    if (innerContent.trim()) {
                      this.getValidation(innerContent).then((result) => {
                        // Trigger re-render when validation completes
                        if (result) {
                          // Force a decoration update by dispatching a no-op transaction
                          view.dispatch({
                            effects: [],
                          })
                        }
                      })
                    }
                  }

                  // Add both mark decoration for styling and replace decoration for functionality
                  // The mark provides the pill styling that persists during tooltip display
                  widgets.push(
                    Decoration.mark({
                      class: `cm-template-pill ${
                        immediateValidation?.isValid === false
                          ? "cm-template-error"
                          : ""
                      }`,
                      inclusive: false,
                    }).range(start, end)
                  )

                  // Also add the widget for interaction (this may be hidden during tooltip)
                  widgets.push(
                    Decoration.replace({
                      widget: widget,
                      inclusive: false,
                    }).range(start, end)
                  )
                }
              }
            },
          })
        }
        return Decoration.set(widgets, true)
      }
    },
    {
      decorations: (v) => v.decorations,
    }
  )
}

async function validateTemplateExpression(
  expression: string,
  workspaceId: string
): Promise<TemplateExpressionValidation> {
  try {
    const response: ExpressionValidationResponse =
      await editorValidateExpression({
        workspaceId,
        requestBody: { expression },
      })

    return {
      isValid: response.is_valid,
      errors: response.errors || [],
      tokens: response.tokens || [],
    }
  } catch (error) {
    console.warn("Expression validation failed:", error)
    return {
      isValid: false,
      errors: [
        {
          loc: [0],
          msg: "Validation service unavailable",
          type: "service_error",
        },
      ],
    }
  }
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
    // Create a container and render the React component into it
    const container = document.createElement("div")
    const root = createRoot(container)
    root.render(component)

    // Return the container itself instead of trying to access firstChild
    return container
  } catch (error) {
    throw error
  }
}

// Template expression suggestions for @ mentions
const templateSuggestions = [
  {
    label: "actions",
    detail: "Previous action results",
    info: "Access results from previous workflow actions",
  },
  {
    label: "trigger",
    detail: "Trigger data",
    info: "Data from the workflow trigger event",
  },
  {
    label: "secrets",
    detail: "Workspace secrets",
    info: "Access configured secrets and credentials",
  },
  {
    label: "inputs",
    detail: "Workflow inputs",
    info: "Input parameters passed to the workflow",
  },
  {
    label: "env",
    detail: "Environment variables",
    info: "System environment variables",
  },
  {
    label: "var",
    detail: "Workflow variables",
    info: "Custom variables defined in the workflow",
  },
  {
    label: "actions.previous.result",
    detail: "Previous action result",
    info: "Result from the immediately previous action",
  },
  {
    label: "trigger.webhook.body",
    detail: "Webhook body",
    info: "Body content from webhook trigger",
  },
  {
    label: "trigger.webhook.headers",
    detail: "Webhook headers",
    info: "Headers from webhook trigger",
  },
  {
    label: "secrets.api_key",
    detail: "API key secret",
    info: "Access API key from secrets",
  },
  {
    label: "inputs.user_id",
    detail: "User ID input",
    info: "User ID parameter from workflow inputs",
  },
  {
    label: "env.NODE_ENV",
    detail: "Environment",
    info: "Current environment (dev/prod)",
  },
]

// Function and action completion cache using existing API
const functionCache = new Map<string, EditorFunctionRead[]>()
const actionCache = new Map<string, EditorActionRead[]>()

async function fetchFunctions(
  workspaceId: string
): Promise<EditorFunctionRead[]> {
  if (functionCache.has(workspaceId)) {
    return functionCache.get(workspaceId)!
  }

  try {
    const functions = await editorListFunctions({ workspaceId })
    functionCache.set(workspaceId, functions)
    return functions
  } catch (error) {
    console.warn("Failed to fetch functions:", error)
    return []
  }
}

// Mention completion function
function mentionCompletion(
  context: CompletionContext
): CompletionResult | null {
  const word = context.matchBefore(/@\w*/)
  if (!word) return null

  if (word.from === word.to && !context.explicit) return null

  return {
    from: word.from,
    options: templateSuggestions.map((suggestion) => ({
      label: `@${suggestion.label}`,
      detail: suggestion.detail,
      info: suggestion.info,
      apply: (view: EditorView, completion: any, from: number, to: number) => {
        // Replace @mention with template expression pill
        const templateExpression = `\${{ ${suggestion.label} }}`
        view.dispatch({
          changes: { from, to, insert: templateExpression },
          selection: { anchor: from + templateExpression.length },
        })
      },
    })),
  }
}

// Function completion for FN context using existing infrastructure
function createFunctionCompletion(workspaceId: string) {
  return async (
    context: CompletionContext
  ): Promise<CompletionResult | null> => {
    // Check for FN.function_name patterns
    const fnWord = context.matchBefore(/FN\.\w*/)
    if (!fnWord) return null

    try {
      const functions = await fetchFunctions(workspaceId)

      // Extract the partial function name after "FN."
      const text = context.state.doc.sliceString(fnWord.from, fnWord.to)
      const partialFunction = text.replace(/^FN\./, "")

      // Filter functions based on partial match
      const filteredFunctions = functions.filter((fn) =>
        fn.name.toLowerCase().startsWith(partialFunction.toLowerCase())
      )

      return {
        from: fnWord.from + 3, // Start after "FN."
        options: filteredFunctions.map((fn) => ({
          label: fn.name,
          detail: fn.return_type || "unknown",
          info: () => {
            const dom = renderTooltipJSX(<FunctionTooltip func={fn} />)
            return { dom }
          },
          apply: (
            view: EditorView,
            completion: any,
            from: number,
            to: number
          ) => {
            // Create parameter snippet similar to suggestions.ts but adapted for CodeMirror
            const params = fn.parameters.map((p) => p.name).join(", ")
            const insertText = `${fn.name}(${params})`

            view.dispatch({
              changes: { from, to, insert: insertText },
              selection: {
                anchor: from + fn.name.length + 1, // Position cursor after opening parenthesis
                head: from + fn.name.length + 1 + params.length, // Select parameters if any
              },
            })
          },
        })),
      }
    } catch (error) {
      console.warn("Failed to get function completions:", error)
      return null
    }
  }
}

// Action completion for ACTIONS context using existing infrastructure
function createActionCompletion(actions: ActionRead[]) {
  return async (
    context: CompletionContext
  ): Promise<CompletionResult | null> => {
    // Check for ACTIONS.action_ref patterns
    const actionWord = context.matchBefore(/ACTIONS\.\w*/)
    if (!actionWord) return null

    try {
      // Extract the partial action ref after "ACTIONS."
      const text = context.state.doc.sliceString(actionWord.from, actionWord.to)
      const partialAction = text.replace(/^ACTIONS\./, "")

      // Filter actions based on partial match
      const filteredActions = actions.filter((action) =>
        action.ref.toLowerCase().startsWith(partialAction.toLowerCase())
      )

      return {
        from: actionWord.from + 8, // Start after "ACTIONS."
        options: filteredActions.map((action) => ({
          label: action.ref,
          detail: action.type || "action",
          info: () => {
            const dom = renderTooltipJSX(<ActionTooltip action={action} />)
            return { dom }
          },
          apply: (
            view: EditorView,
            completion: any,
            from: number,
            to: number
          ) => {
            // Just insert the action reference
            view.dispatch({
              changes: { from, to, insert: action.ref },
              selection: { anchor: from + action.ref.length },
            })
          },
        })),
      }
    } catch (error) {
      console.warn("Failed to get action completions:", error)
      return null
    }
  }
}

// --- State Management for Editable Pill ---
const setEditingRange = StateEffect.define<Range<Decoration> | null>()

const editingRangeField = StateField.define<Range<Decoration> | null>({
  create: () => null,
  update(value, tr) {
    for (const effect of tr.effects) {
      if (effect.is(setEditingRange)) {
        return effect.value
      }
    }
    // If document changes, map the range to the new positions
    if (value && tr.docChanged) {
      // Manually map the 'from' and 'to' positions
      const from = tr.changes.mapPos(value.from, 1)
      const to = tr.changes.mapPos(value.to, -1)
      // If the range was deleted or collapsed, clear it
      if (from >= to) {
        return null
      }
      // Return a new range with the updated positions
      return { from, to, value: value.value }
    }
    return value
  },
  provide: (f) =>
    EditorView.decorations.from(f, (value) => {
      return value ? Decoration.set([value]) : Decoration.none
    }),
})

// Enhanced widget with LSP-based syntax highlighting and error display
class InnerContentWidget extends WidgetType {
  constructor(
    readonly content: string,
    readonly validation?: TemplateExpressionValidation
  ) {
    super()
  }

  toDOM() {
    const span = document.createElement("span")

    // Determine if this expression has errors
    const hasErrors =
      this.validation?.isValid === false ||
      (this.validation?.errors && this.validation.errors.length > 0)

    span.className = `cm-template-pill ${hasErrors ? "cm-template-error" : ""}`

    if (this.validation?.tokens && this.validation.tokens.length > 0) {
      // Render with syntax highlighting from LSP
      this.renderHighlightedContent(span)
    } else {
      // Fallback to plain text
      span.textContent = this.content
    }

    // Add error tooltip and icon if validation failed
    if (hasErrors && this.validation?.errors) {
      const errorMessages = this.validation.errors.map((e) => e.msg).join(", ")
      span.title = `Template Expression Error: ${errorMessages}`

      // Add error indicator
      const errorIcon = document.createElement("span")
      errorIcon.className = "cm-template-error-icon"
      errorIcon.textContent = "⚠"
      errorIcon.title = errorMessages
      span.appendChild(errorIcon)
    }

    return span
  }

  private renderHighlightedContent(container: HTMLElement) {
    if (!this.validation?.tokens) {
      container.textContent = this.content
      return
    }

    // Check if tokens cover the full content to avoid missing prefixes
    const tokensText = this.validation.tokens.map((t) => t.value).join("")
    if (tokensText !== this.content.trim()) {
      // Fallback to plain text if tokens don't represent the full content
      // This prevents issues like "ACTIONS.test.result" showing as ".test.result"
      container.textContent = this.content
      return
    }

    // Create highlighted spans based on LSP tokens
    for (const token of this.validation.tokens) {
      const tokenSpan = document.createElement("span")
      tokenSpan.textContent = token.value
      tokenSpan.className = `cm-token-${token.type.toLowerCase()}`

      // Add error styling if this token has validation errors
      if (this.validation?.errors && this.validation.errors.length > 0) {
        // Check if any error location matches this token position
        const hasError = this.validation.errors.some((error) => {
          if (Array.isArray(error.loc) && error.loc.length > 0) {
            const errorPos = typeof error.loc[0] === "number" ? error.loc[0] : 0
            return errorPos >= token.start && errorPos <= token.end
          }
          return false
        })

        if (hasError) {
          tokenSpan.className += " cm-token-error"
        }
      }

      container.appendChild(tokenSpan)
    }
  }

  eq(other: InnerContentWidget) {
    return (
      other.content === this.content &&
      JSON.stringify(other.validation) === JSON.stringify(this.validation)
    )
  }

  ignoreEvent() {
    return false // We want to handle clicks on the widget
  }
}

// --- Helper to find template at a position ---
function findTemplateAt(
  state: EditorState,
  pos: number
): { from: number; to: number } | null {
  const node = syntaxTree(state).resolve(pos, 1)
  if (node.type.name !== "String") return null

  const stringValue = state.doc.sliceString(node.from + 1, node.to - 1)
  const regex = createTemplateRegex()
  let match
  while ((match = regex.exec(stringValue)) !== null) {
    const from = node.from + 1 + match.index
    const to = from + match[0].length
    if (pos >= from && pos <= to) {
      return { from, to }
    }
  }
  return null
}

// Enhanced arrow key navigation that respects pill boundaries
function enhancedCursorLeft(view: EditorView): boolean {
  const editingRange = view.state.field(editingRangeField)
  const currentPos = view.state.selection.main.head

  // If we're editing a pill and at the start of the inner content
  if (editingRange && currentPos <= editingRange.from + 3) {
    // Exit the pill and move cursor to just before it
    view.dispatch({
      effects: setEditingRange.of(null),
      selection: { anchor: editingRange.from },
    })
    return true
  }

  // If we're not in a pill, check if we're at the right boundary of a template
  if (!editingRange) {
    const templateAtPos = findTemplateAt(view.state, currentPos - 1)
    if (templateAtPos && currentPos === templateAtPos.to) {
      // Enter the pill in editing mode, positioned at the end
      const editingMark = Decoration.mark({
        class: "cm-template-pill cm-template-editing",
      })

      view.dispatch({
        effects: setEditingRange.of(
          editingMark.range(templateAtPos.from, templateAtPos.to)
        ),
        selection: {
          anchor: templateAtPos.to - 3, // Position before " }}"
        },
      })
      return true
    }
  }

  // Default behavior
  return cursorCharLeft(view)
}

function enhancedCursorRight(view: EditorView): boolean {
  const editingRange = view.state.field(editingRangeField)
  const currentPos = view.state.selection.main.head

  // If we're editing a pill and at the end of the inner content
  if (editingRange && currentPos >= editingRange.to - 3) {
    // Exit the pill and move cursor to just after it
    view.dispatch({
      effects: setEditingRange.of(null),
      selection: { anchor: editingRange.to },
    })
    return true
  }

  // If we're not in a pill, check if we're at the left boundary of a template
  if (!editingRange) {
    const templateAtPos = findTemplateAt(view.state, currentPos)
    if (templateAtPos && currentPos === templateAtPos.from) {
      // Enter the pill in editing mode, positioned at the start
      const editingMark = Decoration.mark({
        class: "cm-template-pill cm-template-editing",
      })

      view.dispatch({
        effects: setEditingRange.of(
          editingMark.range(templateAtPos.from, templateAtPos.to)
        ),
        selection: {
          anchor: templateAtPos.from + 3, // Position after "${{ "
        },
      })
      return true
    }
  }

  // Default behavior
  return cursorCharRight(view)
}

// Custom JSON linter with enhanced error reporting
function customJsonLinter(view: EditorView): Diagnostic[] {
  const diagnostics: Diagnostic[] = []
  const content = view.state.doc.toString()

  if (!content.trim()) {
    return diagnostics // Don't lint empty content
  }

  try {
    JSON.parse(content)
  } catch (error: any) {
    let from = 0
    let to = content.length
    let message = "Invalid JSON"

    // Try to extract position information from the error
    if (error.message) {
      message = error.message

      // Parse position from error message (e.g., "Unexpected token } in JSON at position 123")
      const positionMatch = error.message.match(/at position (\d+)/)
      if (positionMatch) {
        const position = parseInt(positionMatch[1])
        from = Math.max(0, position - 1)
        to = Math.min(content.length, position + 1)
      }

      // Parse line/column from error message
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

// Enhanced template expression hover tooltip with function information
const templateExpressionHover = (
  workspaceId: string,
  workflowId: string | null,
  pluginInstance: ViewPlugin<any>
) =>
  hoverTooltip((view, pos, side) => {
    // First find the syntax node at cursor position
    const node = syntaxTree(view.state).resolveInner(pos, -1)

    // Early return if not hovering over a valid node type
    if (
      !["String", "PropertyName", "Literal", "Value", "JsonText"].includes(
        node.type.name
      )
    ) {
      return null
    }

    // Extract string value and find template expression at cursor
    const stringValue = view.state.doc.sliceString(node.from + 1, node.to - 1)
    const regex = createTemplateRegex()
    let match: RegExpExecArray | null
    let tooltipInfo: {
      start: number
      end: number
      validation: TemplateExpressionValidation
      innerContent: string
    } | null = null

    while ((match = regex.exec(stringValue)) !== null) {
      const innerContent = match[1].trim()
      const start = node.from + 1 + match.index
      const end = start + match[0].length

      // Check if hover position is within this template expression
      if (pos >= start && pos <= end) {
        // Get validation from plugin cache
        const plugin = view.plugin(pluginInstance)
        if (plugin) {
          const validation = plugin.validationCache.get(innerContent)
          if (validation) {
            tooltipInfo = { start, end, validation, innerContent }
            break // Found match, exit loop
          }
        }
      }
    }

    if (!tooltipInfo) return null

    return {
      pos: tooltipInfo.start,
      end: tooltipInfo.end,
      create: () => {
        const dom = document.createElement("div")
        dom.className = "cm-template-expression-tooltip"

        const tooltipContent = createTooltipContentJSX(
          tooltipInfo,
          workspaceId,
          workflowId
        )
        const renderedContent = renderTooltipJSX(tooltipContent)

        dom.appendChild(renderedContent)

        return { dom }
      },
    }
  })

// Helper function to create tooltip content with function/action information using JSX
function createTooltipContentJSX(
  info: { validation: TemplateExpressionValidation; innerContent: string },
  workspaceId: string,
  workflowId: string | null
): React.ReactElement {
  let func: EditorFunctionRead | undefined
  let action: EditorActionRead | undefined

  // Check if this is a function call (FN.function_name) - use cached data only
  const fnMatch = info.innerContent.match(/^FN\.(\w+)/)
  if (fnMatch) {
    const functionName = fnMatch[1]
    const cachedFunctions = functionCache.get(workspaceId)
    if (cachedFunctions) {
      func = cachedFunctions.find((f) => f.name === functionName)
    }
  }

  // Check if this is an action reference (ACTIONS.action_ref) - use cached data only
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
  const { theme: appTheme } = useTheme()
  const { workspaceId } = useWorkspace()
  const { workflowId, workflow } = useWorkflow()
  const [editorView, setEditorView] = useState<EditorView | null>(null)
  const [hasErrors, setHasErrors] = useState(false)
  const actions = workflow?.actions || []

  // Ensure value is always a string
  const editorValue =
    typeof value === "string"
      ? value
      : value
        ? JSON.stringify(value, null, 2)
        : ""

  const extensions = useMemo(() => {
    const editingMark = Decoration.mark({
      class: "cm-template-pill cm-template-editing",
    })

    // Plugin to monitor lint diagnostics for errors
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
          // Check for JSON syntax errors by trying to parse
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

    // Create the plugin instance once and reuse it
    const editablePillPluginInstance = editablePillPlugin(workspaceId)
    return [
      // Standard setup extensions
      lintGutter(),
      history(),
      EditorState.allowMultipleSelections.of(true),
      indentUnit.of("  "), // 2 spaces for indentation
      EditorView.lineWrapping, // Enable line wrapping

      // Language and linting
      json(),
      linter(jsonParseLinter()), // Built-in JSON linter
      linter(customJsonLinter), // Custom enhanced JSON linter

      // Keymaps
      keymap.of([
        // Custom pill navigation keybinds (higher priority)
        {
          key: "ArrowLeft",
          run: enhancedCursorLeft,
        },
        {
          key: "ArrowRight",
          run: enhancedCursorRight,
        },
        // Standard keymaps (lower priority)
        ...closeBracketsKeymap,
        ...standardKeymap, // Includes basic editing commands
        ...historyKeymap, // Undo/redo
        ...completionKeymap, // Autocompletion navigation
        indentWithTab, // Allows using Tab for indentation
      ]),

      // Features
      bracketMatching(),
      closeBrackets(),
      autocompletion({
        override: [
          mentionCompletion,
          createFunctionCompletion(workspaceId),
          ...(workflowId
            ? [createActionCompletion(Object.values(actions).map((a) => a))]
            : []),
        ],
      }), // Mention-based autocompletion for template expressions, functions, and actions

      // Custom plugins
      editingRangeField,
      editablePillPluginInstance,
      errorMonitorPlugin,

      // Template expression hover tooltip (pass the plugin instance)
      templateExpressionHover(
        workspaceId,
        workflowId,
        editablePillPluginInstance
      ),

      // Click handling for editable pills
      EditorView.domEventHandlers({
        mousedown: (event, view) => {
          const pos = view.posAtCoords(event)
          if (!pos) return

          const clickedTemplateRange = findTemplateAt(view.state, pos)
          const currentEditingRange = view.state.field(editingRangeField)

          if (
            clickedTemplateRange &&
            (!currentEditingRange ||
              clickedTemplateRange.from !== currentEditingRange.from)
          ) {
            event.preventDefault()

            // Calculate cursor position based on click position within the pill
            const innerStart = clickedTemplateRange.from + 3 // After "${{ "
            const innerEnd = clickedTemplateRange.to - 3 // Before " }}"
            const clickPos = Math.max(innerStart, Math.min(innerEnd, pos))

            view.dispatch({
              effects: setEditingRange.of(
                editingMark.range(
                  clickedTemplateRange.from,
                  clickedTemplateRange.to
                )
              ),
              selection: {
                anchor: clickPos, // Position cursor where user clicked
              },
            })
            return true
          }

          if (!clickedTemplateRange && currentEditingRange) {
            view.dispatch({ effects: setEditingRange.of(null) })
          }
        },
      }),

      // Custom theme for template pills with LSP syntax highlighting
      EditorView.theme({
        ".cm-template-pill": {
          backgroundColor: "rgba(218, 165, 32, 0.2)",
          color: "#8B4513",
          padding: "0.1em 0.4em",
          borderRadius: "6px",
          border: "1px solid rgba(139, 69, 19, 0.3)",
          cursor: "pointer",
          display: "inline-block",
          transition: "all 0.15s ease-in-out",
          position: "relative",
          zIndex: "1",
        },
        ".cm-template-pill:hover": {
          backgroundColor: "rgba(218, 165, 32, 0.3)",
          borderColor: "rgba(139, 69, 19, 0.5)",
          boxShadow: "0 2px 4px rgba(139, 69, 19, 0.2)",
          zIndex: "2",
        },
        ".cm-template-error": {
          backgroundColor: "rgba(239, 68, 68, 0.2)",
          border: "1px solid rgba(239, 68, 68, 0.5)",
          color: "#dc2626",
        },
        ".cm-template-error-icon": {
          color: "#ef4444",
          fontSize: "0.8em",
          marginLeft: "0.2em",
          fontWeight: "bold",
        },
        ".cm-template-editing": {
          padding: "0 !important",
          border: "2px solid rgba(139, 69, 19, 0.8)",
          boxShadow: "0 0 0 3px rgba(218, 165, 32, 0.4)",
          backgroundColor: "rgba(218, 165, 32, 0.1)",
          outline: "none",
          transform: "none",
        },
        // Enhanced lint error styling
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
        // LSP token syntax highlighting
        ".cm-token-keyword": {
          color: "#cf222e",
          fontWeight: "bold",
        },
        ".cm-token-variablename": {
          color: "#0969da",
        },
        ".cm-token-propertyname": {
          color: "#8250df",
        },
        ".cm-token-string": {
          color: "#0a3069",
        },
        ".cm-token-number": {
          color: "#0550ae",
        },
        ".cm-token-bool": {
          color: "#8250df",
        },
        ".cm-token-operator": {
          color: "#cf222e",
        },
        ".cm-token-function": {
          color: "#8250df",
        },
        ".cm-token-punctuation": {
          color: "#656d76",
        },
        ".cm-token-bracket": {
          color: "#656d76",
        },
        // Template expression token errors
        ".cm-token-error": {
          backgroundColor: "rgba(239, 68, 68, 0.3)",
          borderBottom: "1px wavy #ef4444",
          borderRadius: "2px",
        },
        // Template expression hover tooltip styling
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
        // Function completion info styling
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
        // Action completion info styling
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
        // Tooltip function and action info styling
        ".cm-tooltip-function-info": {
          marginBottom: "8px",
          padding: "8px",
          backgroundColor: "rgba(34, 197, 94, 0.1)",
          borderRadius: "4px",
          border: "1px solid rgba(34, 197, 94, 0.3)",
        },
        ".cm-tooltip-action-info": {
          marginBottom: "8px",
          padding: "8px",
          backgroundColor: "rgba(59, 130, 246, 0.1)",
          borderRadius: "4px",
          border: "1px solid rgba(59, 130, 246, 0.3)",
        },
      }),
    ]
  }, [workspaceId, workflowId]) // Include workspaceId and workflowId in dependencies

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
      // If JSON is invalid, we don't format it
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
            // @uiw/react-codemirror specific basicSetup options
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
          }}
          className={cn(
            // Ensure the editor and all its tooltips/autocomplete popups are fully rounded and do not stick out
            "rounded-md text-xs focus-visible:outline-none",
            // Editor container
            "[&_.cm-editor]:rounded-md [&_.cm-editor]:border-0 [&_.cm-focused]:outline-none",
            // Scroller
            "[&_.cm-scroller]:rounded-md",
            // Tooltip (e.g., hover, autocomplete)
            "[&_.cm-tooltip]:rounded-md",
            // Autocomplete suggestion widget and its children
            // Autocomplete tooltip styling
            "[&_.cm-tooltip-autocomplete]:rounded-sm [&_.cm-tooltip-autocomplete]:p-0.5",
            // Autocomplete list styling
            "[&_.cm-tooltip-autocomplete>ul]:rounded-sm",
            // Autocomplete item styling
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

      {/* Floating toolbar - positioned outside the scrollable container */}
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
