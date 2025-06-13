/**
 * Common CodeMirror and Template Expression Utilities
 *
 * This module contains shared functionality for template expression editing,
 * including pill rendering, validation, completion, and interaction logic.
 */
import React from "react"
import {
  ActionRead,
  EditorFunctionRead,
  editorListFunctions,
  editorValidateExpression,
  ExpressionValidationResponse,
} from "@/client"
import {
  Completion,
  CompletionContext,
  CompletionResult,
} from "@codemirror/autocomplete"
import { cursorCharLeft, cursorCharRight } from "@codemirror/commands"
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
  ViewPlugin,
  WidgetType,
  type ViewUpdate,
} from "@codemirror/view"
import {
  AtSignIcon,
  CircleIcon,
  DollarSignIcon,
  FunctionSquareIcon,
  KeyIcon,
} from "lucide-react"
import { createRoot } from "react-dom/client"

// Template expression utilities
export function createTemplateRegex() {
  return /\$\{\{\s*(.*?)\s*\}\}/g
}

const commonIconStyle = {
  className: "m-0 inline size-3 p-0",
  style: {
    verticalAlign: "middle",
    transform: "translateY(0)",
  },
} as const

export const CONTEXT_ICONS = {
  ACTIONS: () => <AtSignIcon {...commonIconStyle} />,
  FN: () => <FunctionSquareIcon {...commonIconStyle} />,
  ENV: () => <DollarSignIcon {...commonIconStyle} />,
  SECRETS: () => <KeyIcon {...commonIconStyle} />,
  var: () => <CircleIcon {...commonIconStyle} />,
} as const

export type ContextType = keyof typeof CONTEXT_ICONS

export function detectContextType(content: string): ContextType | null {
  const trimmed = content.trim()
  for (const context of Object.keys(CONTEXT_ICONS) as ContextType[]) {
    if (
      trimmed.startsWith(`${context}.`) ||
      trimmed.startsWith(`${context}(`)
    ) {
      return context
    }
  }
  return null
}

export function createContextIcon(contextType: ContextType): HTMLElement {
  const iconComponent = CONTEXT_ICONS[contextType]()
  const container = document.createElement("span")
  container.className = "inline"
  container.style.lineHeight = "1"

  const root = createRoot(container)
  root.render(iconComponent)
  return container
}

// Template expression validation
export interface TemplateExpressionValidation {
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

// Caches for functions and actions
export const functionCache = new Map<string, EditorFunctionRead[]>()
export const actionCache = new Map<string, ActionRead[]>()

export async function fetchFunctions(
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

export async function validateTemplateExpression(
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

// State management for editable pills
export const setEditingRange = StateEffect.define<Range<Decoration> | null>()

export const editingRangeField = StateField.define<Range<Decoration> | null>({
  create: () => null,
  update(value, tr) {
    for (const effect of tr.effects) {
      if (effect.is(setEditingRange)) {
        return effect.value
      }
    }
    if (value && tr.docChanged) {
      const from = tr.changes.mapPos(value.from, 1)
      const to = tr.changes.mapPos(value.to, -1)
      if (from >= to) {
        return null
      }
      return { from, to, value: value.value }
    }
    return value
  },
  provide: (f) =>
    EditorView.decorations.from(f, (value) => {
      return value ? Decoration.set([value]) : Decoration.none
    }),
})

// Helper functions
export function findTemplateAt(
  state: EditorState,
  pos: number
): { from: number; to: number } | null {
  const content = state.doc.toString()
  const regex = createTemplateRegex()
  let match
  while ((match = regex.exec(content)) !== null) {
    const from = match.index
    const to = from + match[0].length
    if (pos >= from && pos <= to) {
      return { from, to }
    }
  }
  return null
}

export function enhancedCursorLeft(view: EditorView): boolean {
  const editingRange = view.state.field(editingRangeField)
  const currentPos = view.state.selection.main.head

  if (editingRange && currentPos <= editingRange.from + 3) {
    view.dispatch({
      effects: setEditingRange.of(null),
      selection: { anchor: editingRange.from },
    })
    return true
  }

  if (!editingRange) {
    const templateAtPos = findTemplateAt(view.state, currentPos - 1)
    if (templateAtPos && currentPos === templateAtPos.to) {
      const editingMark = Decoration.mark({
        class: "cm-template-pill cm-template-editing",
      })

      view.dispatch({
        effects: setEditingRange.of(
          editingMark.range(templateAtPos.from, templateAtPos.to)
        ),
        selection: { anchor: templateAtPos.to - 3 },
      })
      return true
    }
  }

  return cursorCharLeft(view)
}

export function enhancedCursorRight(view: EditorView): boolean {
  const editingRange = view.state.field(editingRangeField)
  const currentPos = view.state.selection.main.head

  if (editingRange && currentPos >= editingRange.to - 3) {
    view.dispatch({
      effects: setEditingRange.of(null),
      selection: { anchor: editingRange.to },
    })
    return true
  }

  if (!editingRange) {
    const templateAtPos = findTemplateAt(view.state, currentPos)
    if (templateAtPos && currentPos === templateAtPos.from) {
      const editingMark = Decoration.mark({
        class: "cm-template-pill cm-template-editing",
      })

      view.dispatch({
        effects: setEditingRange.of(
          editingMark.range(templateAtPos.from, templateAtPos.to)
        ),
        selection: { anchor: templateAtPos.from + 3 },
      })
      return true
    }
  }

  return cursorCharRight(view)
}

// Widget for displaying pill content
export class InnerContentWidget extends WidgetType {
  constructor(
    readonly content: string,
    readonly validation?: TemplateExpressionValidation
  ) {
    super()
  }

  toDOM() {
    const span = document.createElement("span")
    const hasErrors =
      this.validation?.isValid === false ||
      (this.validation?.errors && this.validation.errors.length > 0)

    span.className = `cm-template-pill ${hasErrors ? "cm-template-error" : ""}`

    const contextType = detectContextType(this.content)
    if (contextType) {
      span.classList.add(`cm-context-${contextType.toLowerCase()}`)
    }

    this.renderContentWithContextStyling(span, this.content)

    if (hasErrors && this.validation?.errors) {
      const errorMessages = this.validation.errors.map((e) => e.msg).join(", ")
      span.title = `Template Expression Error: ${errorMessages}`

      const errorIcon = document.createElement("span")
      errorIcon.className = "cm-template-error-icon"
      errorIcon.textContent = "⚠"
      errorIcon.title = errorMessages
      span.appendChild(errorIcon)
    }

    return span
  }

  private renderContentWithContextStyling(
    container: HTMLElement,
    content: string
  ) {
    const contextPattern = new RegExp(
      `\\b(${Object.keys(CONTEXT_ICONS).join("|")})\\.(\\w+(?:\\.\\w+)*)`,
      "g"
    )

    let lastIndex = 0
    let match

    while ((match = contextPattern.exec(content)) !== null) {
      const [fullMatch, contextType, path] = match
      const matchStart = match.index
      const matchEnd = match.index + fullMatch.length

      if (matchStart > lastIndex) {
        const beforeText = content.slice(lastIndex, matchStart)
        container.appendChild(document.createTextNode(beforeText))
      }

      const contextSpan = document.createElement("span")
      contextSpan.className = "inline-flex items-center gap-0.5"
      contextSpan.style.lineHeight = "1"

      const iconElement = createContextIcon(contextType as ContextType)
      contextSpan.appendChild(iconElement)

      const pathSpan = document.createElement("span")
      pathSpan.textContent = path
      contextSpan.appendChild(pathSpan)

      const colors = {
        ACTIONS: "#3b82f6",
        FN: "#8b5cf6",
        ENV: "#10b981",
        SECRETS: "#f59e0b",
        var: "#ef4444",
      }

      contextSpan.style.color = colors[contextType as keyof typeof colors]
      contextSpan.style.fontWeight = "500"
      contextSpan.classList.add(`cm-context-${contextType.toLowerCase()}`)

      container.appendChild(contextSpan)
      lastIndex = matchEnd
    }

    if (lastIndex < content.length) {
      const remainingText = content.slice(lastIndex)
      container.appendChild(document.createTextNode(remainingText))
    }
  }

  eq(other: InnerContentWidget) {
    return (
      other.content === this.content &&
      JSON.stringify(other.validation) === JSON.stringify(this.validation)
    )
  }

  ignoreEvent() {
    return false
  }
}

// Template expression pill plugin
export class TemplatePillPluginView {
  decorations: DecorationSet
  validationCache = new Map<string, TemplateExpressionValidation>()
  pendingValidations = new Map<string, Promise<TemplateExpressionValidation>>()

  constructor(
    private view: EditorView,
    private workspaceId: string
  ) {
    this.decorations = this.buildDecorations(view)
  }

  update(update: ViewUpdate): void {
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
    if (this.validationCache.has(expression)) {
      return this.validationCache.get(expression)
    }

    if (this.pendingValidations.has(expression)) {
      return await this.pendingValidations.get(expression)
    }

    const validationPromise = validateTemplateExpression(
      expression,
      this.workspaceId
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

  buildDecorations(view: EditorView): DecorationSet {
    const widgets: Range<Decoration>[] = []
    const editingRange = view.state.field(editingRangeField)

    // This method needs to be implemented differently for JSON vs single-line input
    // We'll provide a default implementation that can be overridden
    return this.buildDecorationsForContent(view, widgets, editingRange)
  }

  protected buildDecorationsForContent(
    view: EditorView,
    widgets: Range<Decoration>[],
    editingRange: Range<Decoration> | null
  ): DecorationSet {
    // Default implementation for plain text content
    const content = view.state.doc.toString()
    const regex = createTemplateRegex()
    let match: RegExpExecArray | null

    while ((match = regex.exec(content)) !== null) {
      const fullMatchText = match[0]
      const innerContent = match[1].trim()
      const start = match.index
      const end = start + fullMatchText.length

      if (
        editingRange &&
        editingRange.from === start &&
        editingRange.to === end
      ) {
        continue
      }

      const validation = this.validationCache.get(innerContent)
      const widget = new InnerContentWidget(innerContent, validation)

      if (!validation && innerContent.trim()) {
        this.getValidation(innerContent).then((result) => {
          if (result) {
            view.dispatch({ effects: [] })
          }
        })
      }

      widgets.push(
        Decoration.mark({
          class: `cm-template-pill ${
            validation?.isValid === false ? "cm-template-error" : ""
          }`,
          inclusive: false,
        }).range(start, end)
      )

      widgets.push(
        Decoration.replace({
          widget: widget,
          inclusive: false,
        }).range(start, end)
      )
    }

    return Decoration.set(widgets, true)
  }
}

export function createTemplatePillPlugin(workspaceId: string) {
  return ViewPlugin.fromClass(
    class extends TemplatePillPluginView {
      constructor(view: EditorView) {
        super(view, workspaceId)
      }
    },
    {
      decorations: (v) => v.decorations,
    }
  )
}

// Template expression suggestions
export const templateSuggestions = [
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
]

// Completion functions
export function createMentionCompletion(): (
  context: CompletionContext
) => CompletionResult | null {
  return (context: CompletionContext): CompletionResult | null => {
    const word = context.matchBefore(/@\w*/)
    if (!word) return null
    if (word.from === word.to && !context.explicit) return null

    return {
      from: word.from,
      options: templateSuggestions.map((suggestion) => ({
        label: `@${suggestion.label}`,
        detail: suggestion.detail,
        info: suggestion.info,
        apply: (
          view: EditorView,
          completion: Completion,
          from: number,
          to: number
        ) => {
          const templateExpression = `\${{ ${suggestion.label} }}`
          view.dispatch({
            changes: { from, to, insert: templateExpression },
            selection: { anchor: from + templateExpression.length },
          })
        },
      })),
    }
  }
}

export function createFunctionCompletion(workspaceId: string) {
  return async (
    context: CompletionContext
  ): Promise<CompletionResult | null> => {
    const fnWord = context.matchBefore(/FN\.\w*/)
    if (!fnWord) return null

    try {
      const functions = await fetchFunctions(workspaceId)
      const text = context.state.doc.sliceString(fnWord.from, fnWord.to)
      const partialFunction = text.replace(/^FN\./, "")

      const filteredFunctions = functions.filter((fn) =>
        fn.name.toLowerCase().startsWith(partialFunction.toLowerCase())
      )

      return {
        from: fnWord.from + 3,
        options: filteredFunctions.map((fn) => ({
          label: fn.name,
          detail: fn.return_type || "unknown",
          apply: (
            view: EditorView,
            completion: Completion,
            from: number,
            to: number
          ) => {
            const params = fn.parameters.map((p) => p.name).join(", ")
            const insertText = `${fn.name}(${params})`
            view.dispatch({
              changes: { from, to, insert: insertText },
              selection: {
                anchor: from + fn.name.length + 1,
                head: from + fn.name.length + 1 + params.length,
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

export function createActionCompletion(actions: ActionRead[]) {
  return async (
    context: CompletionContext
  ): Promise<CompletionResult | null> => {
    const actionWord = context.matchBefore(/ACTIONS\.\w*/)
    if (!actionWord) return null

    try {
      const text = context.state.doc.sliceString(actionWord.from, actionWord.to)
      const partialAction = text.replace(/^ACTIONS\./, "")

      const filteredActions = actions.filter((action) =>
        action.ref.toLowerCase().startsWith(partialAction.toLowerCase())
      )

      return {
        from: actionWord.from + 8,
        options: filteredActions.map((action) => ({
          label: action.ref,
          detail: action.type || "action",
          apply: (
            view: EditorView,
            completion: Completion,
            from: number,
            to: number
          ) => {
            view.dispatch({
              changes: { from, to, insert: `${action.ref}.result` },
              selection: { anchor: from + action.ref.length + 7 },
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

// Enhanced action completion with property completion
export function createEnhancedActionCompletion(actions: ActionRead[]) {
  return async (
    context: CompletionContext
  ): Promise<CompletionResult | null> => {
    // First check for property completion patterns (ACTIONS.actionRef.property)
    const propertyWord = context.matchBefore(/ACTIONS\.\w+\.\w*/)
    if (propertyWord) {
      const text = context.state.doc.sliceString(
        propertyWord.from,
        propertyWord.to
      )
      const match = text.match(/^ACTIONS\.(\w+)\.(\w*)$/)
      if (match) {
        const [, actionRef, partialProperty] = match

        // Check if the action exists
        const action = actions.find((a) => a.ref === actionRef)
        if (action) {
          const properties = ["result", "error", "status"]
          const filteredProperties = properties.filter((prop) =>
            prop.toLowerCase().startsWith(partialProperty.toLowerCase())
          )

          const lastDotIndex = text.lastIndexOf(".")
          return {
            from: propertyWord.from + lastDotIndex + 1,
            options: filteredProperties.map((prop) => ({
              label: prop,
              detail: `${prop} from ${actionRef}`,
              info: `Access the ${prop} from this action`,
            })),
          }
        }
      }
    }

    // Fall back to action reference completion
    const actionWord = context.matchBefore(/ACTIONS\.\w*/)
    if (!actionWord) return null

    try {
      const text = context.state.doc.sliceString(actionWord.from, actionWord.to)
      const partialAction = text.replace(/^ACTIONS\./, "")

      const filteredActions = actions.filter((action) =>
        action.ref.toLowerCase().startsWith(partialAction.toLowerCase())
      )

      return {
        from: actionWord.from + 8,
        options: filteredActions.map((action) => ({
          label: action.ref,
          detail: action.type || "action",
          apply: (
            view: EditorView,
            completion: Completion,
            from: number,
            to: number
          ) => {
            // Add a "." after the action reference to encourage property access
            view.dispatch({
              changes: { from, to, insert: `${action.ref}.` },
              selection: { anchor: from + action.ref.length + 1 },
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

// Click handler for template pills
export function createPillClickHandler() {
  return (event: MouseEvent, view: EditorView): boolean => {
    const pos = view.posAtCoords(event)
    if (!pos) return false

    const clickedTemplateRange = findTemplateAt(view.state, pos)
    const currentEditingRange = view.state.field(editingRangeField)

    if (
      clickedTemplateRange &&
      (!currentEditingRange ||
        clickedTemplateRange.from !== currentEditingRange.from)
    ) {
      event.preventDefault()

      const editingMark = Decoration.mark({
        class: "cm-template-pill cm-template-editing",
      })

      const innerStart = clickedTemplateRange.from + 3
      const innerEnd = clickedTemplateRange.to - 3
      const clickPos = Math.max(innerStart, Math.min(innerEnd, pos))

      view.dispatch({
        effects: setEditingRange.of(
          editingMark.range(clickedTemplateRange.from, clickedTemplateRange.to)
        ),
        selection: { anchor: clickPos },
      })
      return true
    }

    if (!clickedTemplateRange && currentEditingRange) {
      view.dispatch({ effects: setEditingRange.of(null) })
    }

    return false
  }
}

// Common theme for template pills
export const templatePillTheme = EditorView.theme({
  ".cm-template-pill": {
    backgroundColor: "rgba(218, 165, 32, 0.2)",
    color: "#8B4513",
    padding: "0.05em 0.3em",
    borderRadius: "6px",
    border: "1px solid rgba(139, 69, 19, 0.3)",
    cursor: "pointer",
    display: "inline-flex",
    alignItems: "center",
    verticalAlign: "baseline",
    lineHeight: "1.2",
    transition: "all 0.15s ease-in-out",
    position: "relative",
    zIndex: "1",
    minWidth: "fit-content",
    flexShrink: "0",
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
  // Context-specific styling
  ".cm-template-pill.cm-context-actions": {
    color: "#3b82f6 !important",
    fontWeight: "600",
  },
  ".cm-template-pill.cm-context-fn": {
    color: "#8b5cf6 !important",
    fontWeight: "600",
  },
  ".cm-template-pill.cm-context-env": {
    color: "#10b981 !important",
    fontWeight: "600",
  },
  ".cm-template-pill.cm-context-secrets": {
    color: "#f59e0b !important",
    fontWeight: "600",
  },
  ".cm-template-pill.cm-context-var": {
    color: "#ef4444 !important",
    fontWeight: "600",
  },
})
