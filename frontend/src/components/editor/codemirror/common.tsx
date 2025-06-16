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
  SecretReadMinimal,
  secretsListSecrets,
} from "@/client"
import {
  Completion,
  CompletionContext,
  CompletionResult,
  startCompletion,
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
  hoverTooltip,
  keymap,
  ViewPlugin,
  WidgetType,
  type ViewUpdate,
} from "@codemirror/view"
import {
  AtSignIcon,
  BracesIcon,
  DollarSignIcon,
  FunctionSquareIcon,
  KeyIcon,
} from "lucide-react"
import { createRoot } from "react-dom/client"

// Template expression utilities
export function createTemplateRegex() {
  // Match template expressions with named capture groups, equivalent to Python's TEMPLATE_STRING
  return /\$\{\{(\s*(.+?)\s*)\}\}/g
}

/**
 * Parse variable names from for_each expressions
 * Extracts variable names from expressions like "${{ for var.item in collection }}"
 */
export function parseVariablesFromForEach(
  forEachExpressions: string | string[] | null | undefined
): Set<string> {
  const variables = new Set<string>()

  if (!forEachExpressions) {
    return variables
  }

  // Convert to array for uniform processing
  const expressions = Array.isArray(forEachExpressions)
    ? forEachExpressions
    : [forEachExpressions]

  // Regex to match for loop expressions like "${{ for var.variable_name in collection }}"
  const forLoopRegex = /\$\{\{\s*for\s+var\.(\w+)\s+in\s+.+?\s*\}\}/g

  for (const expression of expressions) {
    if (typeof expression === "string" && expression.trim()) {
      let match
      while ((match = forLoopRegex.exec(expression)) !== null) {
        const variableName = match[1]
        if (variableName) {
          variables.add(variableName)
        }
      }
    }
  }

  return variables
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
  var: () => <BracesIcon {...commonIconStyle} />,
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

// Caches for functions, actions, and secrets
export const functionCache = new Map<string, EditorFunctionRead[]>()
export const actionCache = new Map<string, ActionRead[]>()
export const secretsCache = new Map<string, SecretReadMinimal[]>()

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
      value = { from, to, value: value.value }
    }
    if (value) {
      const head = tr.state.selection.main.head
      if (head < value.from || head > value.to) {
        return null
      }
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

    // Safety check: Skip if match contains line breaks
    if (match[0].includes("\n")) {
      continue
    }

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

      // Position cursor at the end of the content, accounting for hidden braces
      const contentEnd = templateAtPos.to - 3
      const content = view.state.doc
        .sliceString(templateAtPos.from + 3, templateAtPos.to - 3)
        .trim()
      const actualContentEnd = templateAtPos.from + 3 + content.length

      view.dispatch({
        effects: setEditingRange.of(
          editingMark.range(templateAtPos.from, templateAtPos.to)
        ),
        selection: { anchor: Math.min(actualContentEnd, contentEnd) },
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

      // Position cursor at the start of the content, accounting for hidden braces
      const contentStart = templateAtPos.from + 3
      const content = view.state.doc.sliceString(
        templateAtPos.from + 3,
        templateAtPos.to - 3
      )
      const firstNonWhitespace = content.search(/\S/)
      const actualContentStart =
        firstNonWhitespace >= 0
          ? contentStart + firstNonWhitespace
          : contentStart

      view.dispatch({
        effects: setEditingRange.of(
          editingMark.range(templateAtPos.from, templateAtPos.to)
        ),
        selection: { anchor: actualContentStart },
      })
      return true
    }
  }

  return cursorCharRight(view)
}

/**
 * Delete the entire template pill when cursor is at either edge and not in edit mode
 */
export function deleteWholePill(view: EditorView, backward: boolean): boolean {
  const editingRange = view.state.field(editingRangeField)

  // Don't interfere with deletion while in edit mode
  if (editingRange) {
    return false
  }

  const pos = view.state.selection.main.head
  const pill = backward
    ? findTemplateAt(view.state, pos - 1) // Backspace → check char before cursor
    : findTemplateAt(view.state, pos) // Delete → check char after cursor

  if (pill) {
    // Check if cursor is at the edge of the pill
    const atLeftEdge = pos === pill.from
    const atRightEdge = pos === pill.to

    if ((backward && atRightEdge) || (!backward && atLeftEdge)) {
      // Delete the entire pill regardless of error state
      view.dispatch({
        changes: { from: pill.from, to: pill.to, insert: "" },
        selection: { anchor: pill.from },
      })
      return true
    }
  }

  return false
}

/**
 * Create keymap for atomic pill deletion
 */
export function createPillDeleteKeymap() {
  return keymap.of([
    {
      key: "Backspace",
      run: (view: EditorView): boolean => deleteWholePill(view, true),
    },
    {
      key: "Delete",
      run: (view: EditorView): boolean => deleteWholePill(view, false),
    },
  ])
}

/**
 * Invisible widget used to hide template expression braces during editing.
 * Renders with zero width but maintains cursor positioning.
 */
class InvisibleWidget extends WidgetType {
  toDOM(): HTMLElement {
    const span = document.createElement("span")
    span.style.width = "0"
    span.style.height = "1em" // maintain line height for cursor
    span.style.overflow = "hidden"
    span.style.display = "inline-block"
    return span
  }

  ignoreEvent(): boolean {
    return true
  }
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

    // Hide braces when in edit mode
    if (editingRange) {
      // opening '${{'
      widgets.push(
        Decoration.replace({
          widget: new InvisibleWidget(),
          inclusive: false,
        }).range(editingRange.from, editingRange.from + 3)
      )
      // closing '}}'
      widgets.push(
        Decoration.replace({
          widget: new InvisibleWidget(),
          inclusive: false,
        }).range(editingRange.to - 3, editingRange.to)
      )
    }

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

      // Safety check: Skip if match contains line breaks
      if (fullMatchText.includes("\n")) {
        continue
      }

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

// Create hover tooltips for individual expression nodes within template pills
export function createExpressionNodeHover(workspaceId: string) {
  return hoverTooltip((view, pos, side) => {
    // Find if the position is within a template expression
    const doc = view.state.doc
    const line = doc.lineAt(pos)
    const lineText = line.text

    // Find all template expressions in the line
    const templateRegex = createTemplateRegex()
    let match
    templateRegex.lastIndex = 0

    while ((match = templateRegex.exec(lineText)) !== null) {
      const templateStart = line.from + match.index
      const templateEnd = templateStart + match[0].length

      // Check if position is within this template expression
      if (pos >= templateStart && pos <= templateEnd) {
        const innerContent = match[1].trim()
        const innerStart = templateStart + match[0].indexOf(innerContent)
        const innerEnd = innerStart + innerContent.length

        // Check if position is within the inner content
        if (pos >= innerStart && pos <= innerEnd) {
          const relativePos = pos - innerStart
          return createNodeTooltipForPosition(
            view,
            innerContent,
            relativePos,
            templateStart,
            workspaceId
          )
        }
      }
    }

    return null
  })
}

// Helper function to create tooltip for a specific position within an expression
async function createNodeTooltipForPosition(
  view: EditorView,
  expression: string,
  relativePos: number,
  templateStart: number,
  workspaceId: string
) {
  try {
    // Get validation which includes tokens
    const validation = await validateTemplateExpression(expression, workspaceId)

    if (!validation.tokens || validation.tokens.length === 0) {
      return null
    }

    // Find all tokens that contain the position
    const candidateTokens = validation.tokens.filter(
      (token) => relativePos >= token.start && relativePos <= token.end
    )

    if (candidateTokens.length === 0) {
      return null
    }

    // Choose the most complete/longest token that contains the position
    // This ensures we show tooltip for "ACTIONS.test.result" when hovering over "test" or "result"
    const targetToken = candidateTokens.reduce((longest, current) => {
      const currentLength = current.end - current.start
      const longestLength = longest.end - longest.start

      // Prefer tokens that start with context keywords (ACTIONS, FN, etc.)
      const currentIsContextToken = current.value.match(
        /^(ACTIONS|FN|SECRETS|ENV|TRIGGER|var)\b/
      )
      const longestIsContextToken = longest.value.match(
        /^(ACTIONS|FN|SECRETS|ENV|TRIGGER|var)\b/
      )

      if (currentIsContextToken && !longestIsContextToken) {
        return current
      }
      if (!currentIsContextToken && longestIsContextToken) {
        return longest
      }

      // If both or neither are context tokens, prefer the longer one
      return currentLength > longestLength ? current : longest
    })

    // Create tooltip based on token type
    console.log({ targetToken, expression })
    const tooltipContent = await createNodeTooltipContent(
      targetToken,
      expression,
      workspaceId
    )

    if (!tooltipContent) {
      return null
    }

    return {
      pos: templateStart + 3 + targetToken.start, // Account for ${{ prefix
      end: templateStart + 3 + targetToken.end,
      above: true,
      create: () => ({
        dom: tooltipContent,
      }),
    }
  } catch (error) {
    console.warn("Failed to create node tooltip:", error)
    return null
  }
}

// Create tooltip content for a specific token/node
async function createNodeTooltipContent(
  token: { type: string; value: string; start: number; end: number },
  fullExpression: string,
  workspaceId: string
): Promise<HTMLElement | null> {
  const container = document.createElement("div")
  container.className = "cm-expression-node-tooltip"

  // Header showing the token value and type
  const header = document.createElement("div")
  header.className = "cm-tooltip-header"
  header.textContent = `${token.value} (${token.type})`
  container.appendChild(header)

  // Detect expression patterns in the token value, not just the type
  const tokenValue = token.value

  // Add type-specific information based on value patterns
  if (fullExpression.startsWith("ACTIONS.")) {
    addActionTooltipInfo(container, tokenValue, workspaceId)
  } else if (fullExpression.startsWith("FN.")) {
    await addFunctionTooltipInfo(container, tokenValue, workspaceId)
  } else if (fullExpression.startsWith("SECRETS.")) {
    addSecretTooltipInfo(container, tokenValue)
  } else if (fullExpression.startsWith("ENV.")) {
    addEnvTooltipInfo(container, tokenValue)
  } else if (fullExpression.startsWith("TRIGGER")) {
    addTriggerTooltipInfo(container, tokenValue)
  } else if (
    token.type === "ACTIONS" ||
    token.type === "FN" ||
    token.type === "SECRETS" ||
    token.type === "ENV" ||
    token.type === "TRIGGER"
  ) {
    // Handle context types even without dot notation
    if (token.type === "ACTIONS") {
      addActionTooltipInfo(container, tokenValue, workspaceId)
    } else if (token.type === "FN") {
      await addFunctionTooltipInfo(container, tokenValue, workspaceId)
    } else if (token.type === "SECRETS") {
      addSecretTooltipInfo(container, tokenValue)
    } else if (token.type === "ENV") {
      addEnvTooltipInfo(container, tokenValue)
    } else if (token.type === "TRIGGER") {
      addTriggerTooltipInfo(container, tokenValue)
    }
  } else {
    // Generic token info
    const info = document.createElement("div")
    info.className = "cm-tooltip-generic-info"
    info.textContent = `Token type: ${token.type}`
    container.appendChild(info)
  }

  return container
}

// Helper functions for type-specific tooltip content
function addActionTooltipInfo(
  container: HTMLElement,
  value: string,
  workspaceId: string
) {
  const info = document.createElement("div")
  info.className = "cm-tooltip-action-info"

  // Enhanced regex to capture more complex action paths
  const match = value.match(/ACTIONS\.(\w+)(?:\.(.+))?/)
  if (match) {
    const [, actionRef, propertyPath] = match
    info.innerHTML = `
      <div class="action-ref">Action: <strong>${actionRef}</strong></div>
      ${propertyPath ? `<div class="action-prop">Property: <strong>${propertyPath}</strong></div>` : ""}
      <div class="action-desc">References output from action step</div>
    `
  } else {
    // Fallback for partial matches or just "ACTIONS"
    const cleanValue = value.replace(/^ACTIONS\.?/, "")
    if (cleanValue) {
      info.innerHTML = `
        <div class="action-ref">Action reference: <strong>${cleanValue}</strong></div>
        <div class="action-desc">References output from action step</div>
      `
    } else {
      info.innerHTML = `
        <div class="action-ref">Action namespace</div>
        <div class="action-desc">Used to reference outputs from workflow actions</div>
      `
    }
  }

  container.appendChild(info)
}

async function addFunctionTooltipInfo(
  container: HTMLElement,
  fnName: string,
  workspaceId: string
) {
  const info = document.createElement("div")
  info.className = "cm-tooltip-function-info"
  // Try to get cached function info
  const functions = await fetchFunctions(workspaceId)
  if (functions) {
    const func = functions.find((f) => f.name === fnName)
    if (func) {
      const root = createRoot(info)
      root.render(<FunctionTooltip fn={func} />)
    }
  } else {
    // Fallback for partial matches or just "FN"
    const cleanValue = fnName.replace(/^FN\.?/, "")
    const root = createRoot(info)
    root.render(
      cleanValue ? (
        <div>
          <div className="function-name">
            Function reference: <strong>{cleanValue}</strong>
          </div>
          <div className="function-desc">
            Built-in function for data processing
          </div>
        </div>
      ) : (
        <div>
          <div className="function-name">Function namespace</div>
          <div className="function-desc">
            Used to call built-in functions for data processing
          </div>
        </div>
      )
    )
  }

  container.appendChild(info)
}

function addSecretTooltipInfo(container: HTMLElement, value: string) {
  const info = document.createElement("div")
  info.className = "cm-tooltip-secret-info"

  const match = value.match(/SECRETS\.(\w+)(?:\.(.+))?/)
  if (match) {
    const [, secretName, key] = match
    info.innerHTML = `
      <div class="secret-name">Secret: <strong>${secretName}</strong></div>
      ${key ? `<div class="secret-key">Key: <strong>${key}</strong></div>` : ""}
      <div class="secret-desc">References stored secret credential</div>
    `
  } else {
    // Fallback for partial matches or just "SECRETS"
    const cleanValue = value.replace(/^SECRETS\.?/, "")
    if (cleanValue) {
      info.innerHTML = `
        <div class="secret-name">Secret reference: <strong>${cleanValue}</strong></div>
        <div class="secret-desc">References stored secret credential</div>
      `
    } else {
      info.innerHTML = `
        <div class="secret-name">Secrets namespace</div>
        <div class="secret-desc">Used to reference stored secret credentials</div>
      `
    }
  }

  container.appendChild(info)
}

function addEnvTooltipInfo(container: HTMLElement, value: string) {
  const info = document.createElement("div")
  info.className = "cm-tooltip-env-info"

  const match = value.match(/ENV\.(.+)/)
  if (match) {
    const [, envPath] = match
    info.innerHTML = `
      <div class="env-path">Path: <strong>${envPath}</strong></div>
      <div class="env-desc">References environment variable or configuration</div>
    `
  } else {
    // Fallback for partial matches or just "ENV"
    const cleanValue = value.replace(/^ENV\.?/, "")
    if (cleanValue) {
      info.innerHTML = `
        <div class="env-path">Environment variable: <strong>${cleanValue}</strong></div>
        <div class="env-desc">References environment variable or configuration</div>
      `
    } else {
      info.innerHTML = `
        <div class="env-path">Environment namespace</div>
        <div class="env-desc">Used to reference environment variables and configuration</div>
      `
    }
  }

  container.appendChild(info)
}

function addTriggerTooltipInfo(container: HTMLElement, value: string) {
  const info = document.createElement("div")
  info.className = "cm-tooltip-trigger-info"

  const match = value.match(/TRIGGER(?:\.(.+))?/)
  if (match) {
    const [, triggerPath] = match
    info.innerHTML = `
      ${triggerPath ? `<div class="trigger-path">Path: <strong>${triggerPath}</strong></div>` : '<div class="trigger-root">Trigger data</div>'}
      <div class="trigger-desc">References workflow trigger input data</div>
    `
  } else {
    info.textContent = "Trigger reference"
  }

  container.appendChild(info)
}

// Template expression suggestions
export const TEMPLATE_SUGGESTIONS = [
  {
    label: "ACTIONS",
    detail: "Previous action results",
    info: "Access results from previous workflow actions",
  },
  {
    label: "FN",
    detail: "Built-in functions",
    info: "Built-in functions for data processing",
  },
  {
    label: "TRIGGER",
    detail: "Trigger input data",
    info: "Data from the workflow trigger event",
  },
  {
    label: "SECRETS",
    detail: "Workspace secrets",
    info: "Access configured secrets and credentials",
  },
  {
    label: "ENV",
    detail: "Runtime environment variables",
    info: "Environment variables available at runtime",
  },
  {
    label: "var",
    detail: "Workflow variables",
    info: "Custom variables defined in the workflow",
  },
  {
    label: "foreach",
    detail: "For each item in the array",
    info: "For each item in the array",
  },
]

// Custom keymap for @ key to trigger completions
export function createAtKeyCompletion() {
  return keymap.of([
    {
      key: "@",
      run: (view: EditorView): boolean => {
        // Insert the @ character first
        const pos = view.state.selection.main.head
        view.dispatch({
          changes: { from: pos, insert: "@" },
          selection: { anchor: pos + 1 },
        })

        // Trigger completion after a short delay to allow the @ to be processed
        setTimeout(() => {
          startCompletion(view)
        }, 10)

        return true
      },
    },
  ])
}

// Custom keymap for Escape key to exit editing mode
export function createExitEditModeKeyHandler() {
  return keymap.of([
    {
      key: "Escape",
      run: (view: EditorView): boolean => {
        const currentEditingRange = view.state.field(editingRangeField)
        if (currentEditingRange) {
          // Directly clear the editing state
          view.dispatch({ effects: setEditingRange.of(null) })
          return true
        }
        return false
      },
    },
  ])
}

// Completion functions
export function createMentionCompletion(): (
  context: CompletionContext
) => CompletionResult | null {
  return (context: CompletionContext): CompletionResult | null => {
    const word = context.matchBefore(/@\w*/)
    if (!word) return null
    if (word.from === word.to && !context.explicit) return null

    // Check if we're inside a template expression
    const cursorPos = context.pos
    const templateRange = findTemplateAt(context.state, cursorPos)
    const isInsideTemplate = templateRange !== null

    return {
      from: word.from,
      options: TEMPLATE_SUGGESTIONS.map((suggestion) => ({
        label: `@${suggestion.label}`,
        detail: suggestion.detail,
        info: suggestion.info,
        apply: (
          view: EditorView,
          completion: Completion,
          from: number,
          to: number
        ) => {
          // Special handling for foreach
          if (suggestion.label === "foreach") {
            const itemPlaceholder = "_item_"
            const collectionPlaceholder = "_collection_"
            const foreachExpression = `for var.${itemPlaceholder} in ${collectionPlaceholder}`

            if (isInsideTemplate) {
              // Inside template: just insert and select first placeholder
              view.dispatch({
                changes: { from, to, insert: foreachExpression },
                selection: { anchor: from + 8, head: from + 14 }, // Select "_item_"
              })
            } else {
              // Outside template: wrap with ${{ }} and select first placeholder
              const templateExpression = `\${{ ${foreachExpression} }}`
              const templateStart = from
              const templateEnd = from + templateExpression.length

              // Create editing mark decoration
              const editingMark = Decoration.mark({
                class: "cm-template-pill cm-template-editing",
              })

              view.dispatch({
                changes: { from, to, insert: templateExpression },
                effects: setEditingRange.of(
                  editingMark.range(templateStart, templateEnd)
                ),
                selection: {
                  anchor: from + 12, // Position at start of "_item_" (after "${{ for var.")
                  head: from + 18, // Select "_item_"
                },
              })
            }
            return
          }

          // For contexts that have properties, automatically add a dot to enable immediate property/function completion
          const needsDot =
            suggestion.label === "ACTIONS" ||
            suggestion.label === "FN" ||
            suggestion.label === "SECRETS" ||
            suggestion.label === "ENV" ||
            suggestion.label === "var"
          const labelWithDot = needsDot
            ? `${suggestion.label}.`
            : suggestion.label

          if (isInsideTemplate) {
            // Inside template: only insert the label without wrapping
            view.dispatch({
              changes: { from, to, insert: labelWithDot },
              selection: { anchor: from + labelWithDot.length },
            })
          } else {
            // Outside template: wrap with ${{ }} and create editing mode
            const templateExpression = `\${{ ${labelWithDot} }}`
            const templateStart = from
            const templateEnd = from + templateExpression.length
            const cursorPosition = from + 4 + labelWithDot.length // Position after "${{ <label>." or "${{ <label>"

            // Create editing mark decoration
            const editingMark = Decoration.mark({
              class: "cm-template-pill cm-template-editing",
            })

            view.dispatch({
              changes: { from, to, insert: templateExpression },
              effects: setEditingRange.of(
                editingMark.range(templateStart, templateEnd)
              ),
              selection: { anchor: cursorPosition },
            })
          }
          // For labels that need dots, trigger completion after a short delay
          if (needsDot) {
            startCompletion(view)
          }
        },
      })),
    }
  }
}
function FunctionTooltip({ fn }: { fn: EditorFunctionRead }) {
  // Function signature
  const params = fn.parameters
    .map((p) => `${p.name}: ${p.type || "any"}`)
    .join(", ")
  const signature = `${fn.name}(${params}) → ${fn.return_type || "unknown"}`
  return (
    <div className="max-w-[400px] overflow-hidden p-0 text-xs">
      <div className="border-b px-3 py-2 font-mono font-semibold text-[#24292f]">
        {fn.name}
      </div>
      <div className="border-b px-3 py-2 font-mono text-[11px] text-[#656d76]">
        {signature}
      </div>
      <div className="p-3 text-xs leading-6 text-[#24292f]">
        {fn.description || "No description available"}
      </div>
      {/*parameters*/}
      <div className="px-3 py-2 font-mono text-[11px] text-[#656d76]">
        Parameters: {params}
      </div>
      {/*return type*/}
      <div className="px-3 py-2 font-mono text-[11px] text-[#656d76]">
        Returns: {fn.return_type || "unknown"}
      </div>
    </div>
  )
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
          info: () => {
            const container = document.createElement("div")
            container.className = "cm-function-info-tooltip"
            const root = createRoot(container)
            root.render(<FunctionTooltip fn={fn} />)
            return container
          },
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

export async function fetchSecrets(
  workspaceId: string
): Promise<SecretReadMinimal[]> {
  if (secretsCache.has(workspaceId)) {
    return secretsCache.get(workspaceId)!
  }

  try {
    // Import the secrets function from the client
    const secrets = await secretsListSecrets({
      workspaceId,
      type: ["custom"],
    })
    secretsCache.set(workspaceId, secrets)
    return secrets
  } catch (error) {
    console.warn("Failed to fetch secrets:", error)
    return []
  }
}

export function createSecretsCompletion(workspaceId: string) {
  return async (
    context: CompletionContext
  ): Promise<CompletionResult | null> => {
    // Try to match secret key pattern first (SECRETS.secret_name.key)
    const secretKeyWord = context.matchBefore(/SECRETS\.\w+\.\w*/)
    if (secretKeyWord) {
      try {
        const secrets = await fetchSecrets(workspaceId)
        const text = context.state.doc.sliceString(
          secretKeyWord.from,
          secretKeyWord.to
        )

        // Extract secret name and partial key from the text
        const match = text.match(/^SECRETS\.(\w+)\.(\w*)$/)
        if (!match) return null

        const [, secretName, partialKey] = match

        // Find the specific secret
        const secret = secrets.find((s) => s.name === secretName)
        if (!secret || !secret.keys) return null

        // Filter keys based on partial input
        const filteredKeys = secret.keys.filter((key) =>
          key.toLowerCase().startsWith(partialKey.toLowerCase())
        )

        if (filteredKeys.length === 0) return null

        // Calculate the position to insert from (after the last dot)
        const lastDotIndex = text.lastIndexOf(".")
        const keyStartPosition = secretKeyWord.from + lastDotIndex + 1

        return {
          from: keyStartPosition,
          options: filteredKeys.map((key) => ({
            label: key,
            detail: "secret key",
            info: `Secret key: ${secretName}.${key}`,
            apply: (
              view: EditorView,
              completion: Completion,
              from: number,
              to: number
            ) => {
              view.dispatch({
                changes: { from, to, insert: key },
                selection: { anchor: from + key.length },
              })
            },
          })),
        }
      } catch (error) {
        console.warn("Failed to get secret key completions:", error)
        return null
      }
    }

    // Fall back to secret name completion (SECRETS.secret_name)
    const secretWord = context.matchBefore(/SECRETS\.\w*/)
    if (!secretWord) return null

    try {
      const secrets = await fetchSecrets(workspaceId)
      const text = context.state.doc.sliceString(secretWord.from, secretWord.to)
      const partialSecret = text.replace(/^SECRETS\./, "")

      const filteredSecrets = secrets.filter((secret) =>
        secret.name.toLowerCase().startsWith(partialSecret.toLowerCase())
      )

      return {
        from: secretWord.from + 8,
        options: filteredSecrets.map((secret) => {
          const hasKeys = secret.keys && secret.keys.length > 0
          return {
            label: secret.name,
            detail: "secret",
            info: `Secret: ${secret.name}${secret.description ? ` - ${secret.description}` : ""}\nKeys: ${secret.keys?.join(", ") || "none"}`,
            apply: (
              view: EditorView,
              completion: Completion,
              from: number,
              to: number
            ) => {
              // Add dot for secrets that have keys to trigger key completion
              const insertText = hasKeys ? `${secret.name}.` : secret.name

              view.dispatch({
                changes: { from, to, insert: insertText },
                selection: { anchor: from + insertText.length },
              })

              // Trigger key completion for secrets with keys
              if (hasKeys) {
                startCompletion(view)
              }
            },
          }
        }),
      }
    } catch (error) {
      console.warn("Failed to get secrets completions:", error)
      return null
    }
  }
}

export function createEnvCompletion() {
  return async (
    context: CompletionContext
  ): Promise<CompletionResult | null> => {
    // Try to match nested workflow properties first (ENV.workflow.property)
    const workflowPropWord = context.matchBefore(/ENV\.workflow\.\w*/)
    if (workflowPropWord) {
      const workflowProperties = [
        {
          label: "start_time",
          detail: "Workflow start time",
          info: "ISO timestamp when the workflow execution started",
        },
        {
          label: "dispatch_type",
          detail: "Dispatch type",
          info: "How the workflow was triggered (e.g., 'manual', 'webhook')",
        },
        {
          label: "execution_id",
          detail: "Execution ID",
          info: "Unique identifier for this workflow execution",
        },
        {
          label: "run_id",
          detail: "Run ID",
          info: "Unique identifier for this workflow run",
        },
        {
          label: "trigger_type",
          detail: "Trigger type",
          info: "Type of trigger that initiated this workflow",
        },
      ]

      const text = context.state.doc.sliceString(
        workflowPropWord.from,
        workflowPropWord.to
      )
      const partialProp = text.replace(/^ENV\.workflow\./, "")

      const filteredProps = workflowProperties.filter((prop) =>
        prop.label.toLowerCase().startsWith(partialProp.toLowerCase())
      )

      return {
        from: workflowPropWord.from + 13, // "ENV.workflow.".length
        options: filteredProps.map((prop) => ({
          label: prop.label,
          detail: prop.detail,
          info: prop.info,
          apply: (
            view: EditorView,
            completion: Completion,
            from: number,
            to: number
          ) => {
            view.dispatch({
              changes: { from, to, insert: prop.label },
              selection: { anchor: from + prop.label.length },
            })
          },
        })),
      }
    }

    // Fall back to top-level ENV properties
    const envWord = context.matchBefore(/ENV\.\w*/)
    if (!envWord) return null

    // Based on DSLEnvironment from workflow.py, provide common environment variables
    const envSuggestions = [
      {
        label: "workflow",
        detail: "Workflow metadata",
        info: "Contains workflow execution metadata (start_time, dispatch_type, execution_id, run_id, trigger_type)",
        hasChildren: true,
      },
      {
        label: "environment",
        detail: "Target environment",
        info: "The current workflow environment (e.g., 'default', 'production')",
        hasChildren: false,
      },
      {
        label: "registry_version",
        detail: "Registry version",
        info: "The registry version used for this workflow",
        hasChildren: false,
      },
    ]

    const text = context.state.doc.sliceString(envWord.from, envWord.to)
    const partialEnv = text.replace(/^ENV\./, "")

    const filteredSuggestions = envSuggestions.filter((suggestion) =>
      suggestion.label.toLowerCase().startsWith(partialEnv.toLowerCase())
    )

    return {
      from: envWord.from + 4,
      options: filteredSuggestions.map((suggestion) => ({
        label: suggestion.label,
        detail: suggestion.detail,
        info: suggestion.info,
        apply: (
          view: EditorView,
          completion: Completion,
          from: number,
          to: number
        ) => {
          // Add dot for properties that have children to trigger nested completion
          const insertText = suggestion.hasChildren
            ? `${suggestion.label}.`
            : suggestion.label

          view.dispatch({
            changes: { from, to, insert: insertText },
            selection: { anchor: from + insertText.length },
          })

          // Trigger completion for nested properties
          if (suggestion.hasChildren) {
            startCompletion(view)
          }
        },
      })),
    }
  }
}

export function createVarCompletion(
  forEachExpressions?: string | string[] | null | undefined
) {
  return async (
    context: CompletionContext
  ): Promise<CompletionResult | null> => {
    const varWord = context.matchBefore(/var\.\w*/)
    if (!varWord) return null

    // Parse variables from the current form's for_each expressions
    const parsedVariables = parseVariablesFromForEach(forEachExpressions)

    // Add common variable patterns as fallback if no parsed variables
    const fallbackVariables = new Set<string>([
      "item",
      "index",
      "key",
      "value",
      "element",
      "entry",
      "record",
      "data",
    ])

    // Use parsed variables if available, otherwise fall back to common patterns
    const variables =
      parsedVariables.size > 0 ? parsedVariables : fallbackVariables

    const text = context.state.doc.sliceString(varWord.from, varWord.to)
    const partialVar = text.replace(/^var\./, "")

    const filteredVars = Array.from(variables).filter((varName) =>
      varName.toLowerCase().startsWith(partialVar.toLowerCase())
    )

    return {
      from: varWord.from + 4,
      options: filteredVars.map((varName) => ({
        label: varName,
        detail: parsedVariables.has(varName) ? "loop variable" : "variable",
        info: parsedVariables.has(varName)
          ? `Loop variable from for_each: ${varName}`
          : `Common loop variable: ${varName}`,
        apply: (
          view: EditorView,
          completion: Completion,
          from: number,
          to: number
        ) => {
          view.dispatch({
            changes: { from, to, insert: varName },
            selection: { anchor: from + varName.length },
          })
        },
      })),
    }
  }
}

// Click handler for template pills
export function createPillClickHandler() {
  return (event: MouseEvent, view: EditorView): boolean => {
    const pos = view.posAtCoords({ x: event.clientX, y: event.clientY })
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
      // Ensure click position is within the content area, accounting for hidden braces
      const content = view.state.doc.sliceString(innerStart, innerEnd)
      const contentLength = content.length
      const adjustedPos = Math.max(
        innerStart,
        Math.min(innerEnd, pos, innerStart + contentLength)
      )

      view.dispatch({
        effects: setEditingRange.of(
          editingMark.range(clickedTemplateRange.from, clickedTemplateRange.to)
        ),
        selection: { anchor: adjustedPos },
      })
      return true
    }

    if (!clickedTemplateRange && currentEditingRange) {
      view.dispatch({ effects: setEditingRange.of(null) })
    }

    return false
  }
}

// Blur handler to clear editing state
export function createBlurHandler() {
  return (event: FocusEvent, view: EditorView): boolean => {
    const currentEditingRange = view.state.field(editingRangeField)
    if (currentEditingRange) {
      view.dispatch({ effects: setEditingRange.of(null) })
    }
    return false
  }
}

// Common theme for template pills
export const templatePillTheme = EditorView.theme({
  ".cm-template-pill": {
    backgroundColor: "rgba(59, 130, 246, 0.15)",
    color: "#1e40af",
    padding: "0.075em 0.3em",
    borderRadius: "0.25rem",
    border: "0.5px solid rgba(59, 130, 246, 0.3)",
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
    fontFamily: "ui-monospace, monospace",
  },
  ".cm-template-pill:hover": {
    backgroundColor: "rgba(59, 130, 246, 0.25)",
    borderColor: "rgba(59, 130, 246, 0.5)",
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
    border: "1px solid rgba(59, 130, 246, 0.8)",
    backgroundColor: "rgba(59, 130, 246, 0.1)",
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
  ".cm-expression-node-tooltip": {
    backgroundColor: "hsl(var(--popover))",
    color: "hsl(var(--popover-foreground))",
    border: "0.5px solid hsl(var(--border))",
    borderRadius: "6px",
    padding: "8px 12px",
    fontSize: "12px",
    maxWidth: "300px",
    boxShadow:
      "0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06)",
    fontFamily: "ui-sans-serif, system-ui, sans-serif",
  },
  ".cm-expression-node-tooltip .cm-tooltip-header": {
    fontWeight: "600",
    marginBottom: "6px",
    color: "hsl(var(--foreground))",
    borderBottom: "1px solid hsl(var(--border))",
    paddingBottom: "4px",
    fontFamily: "ui-monospace, monospace",
  },
  ".cm-tooltip-action-info": {
    color: "#93c5fd",
  },
  ".cm-tooltip-action-info .action-ref": {
    marginBottom: "2px",
  },
  ".cm-tooltip-action-info .action-prop": {
    marginBottom: "2px",
    color: "#ddd6fe",
  },
  ".cm-tooltip-action-info .action-desc": {
    fontSize: "11px",
    color: "#9ca3af",
    fontStyle: "italic",
  },
  ".cm-tooltip-function-info": {
    color: "#9ca3af",
  },
  ".cm-tooltip-function-info .function-name": {
    marginBottom: "2px",
  },
  ".cm-tooltip-function-info .function-params": {
    marginBottom: "2px",
    color: "#a5b4fc",
    fontSize: "11px",
  },
  ".cm-tooltip-function-info .function-params code": {
    backgroundColor: "rgba(139, 92, 246, 0.2)",
    padding: "1px 4px",
    borderRadius: "3px",
    fontFamily: "ui-monospace, monospace",
  },
  ".cm-tooltip-function-info .function-desc": {
    fontSize: "11px",
    color: "#9ca3af",
    fontStyle: "italic",
  },
  ".cm-tooltip-function-info .function-description": {
    fontSize: "11px",
    color: "#d1d5db",
    marginTop: "4px",
  },
  ".cm-tooltip-secret-info": {
    color: "#fbbf24",
  },
  ".cm-tooltip-secret-info .secret-name": {
    marginBottom: "2px",
  },
  ".cm-tooltip-secret-info .secret-key": {
    marginBottom: "2px",
    color: "#fed7aa",
  },
  ".cm-tooltip-secret-info .secret-desc": {
    fontSize: "11px",
    color: "#9ca3af",
    fontStyle: "italic",
  },
  ".cm-tooltip-env-info": {
    color: "#6ee7b7",
  },
  ".cm-tooltip-env-info .env-path": {
    marginBottom: "2px",
  },
  ".cm-tooltip-env-info .env-desc": {
    fontSize: "11px",
    color: "#9ca3af",
    fontStyle: "italic",
  },
  ".cm-tooltip-trigger-info": {
    color: "#f472b6",
  },
  ".cm-tooltip-trigger-info .trigger-path": {
    marginBottom: "2px",
  },
  ".cm-tooltip-trigger-info .trigger-root": {
    marginBottom: "2px",
  },
  ".cm-tooltip-trigger-info .trigger-desc": {
    fontSize: "11px",
    color: "#9ca3af",
    fontStyle: "italic",
  },
  ".cm-tooltip-generic-info": {
    color: "#d1d5db",
    fontSize: "11px",
  },
})

export const EDITOR_STYLE = `
  rounded-md text-xs focus-visible:outline-none
  [&_.cm-editor]:rounded-md
  [&_.cm-editor]:border-0
  [&_.cm-focused]:outline-none
  [&_.cm-focused]:ring-2
  [&_.cm-focused]:ring-ring
  [&_.cm-focused]:ring-offset-2
  [&_.cm-scroller]:rounded-md
  [&_.cm-tooltip]:rounded-md
  [&_.cm-tooltip]:border
  [&_.cm-tooltip]:border-border
  [&_.cm-tooltip-autocomplete]:rounded-sm
  [&_.cm-tooltip-autocomplete]:border
  [&_.cm-tooltip-autocomplete]:border-input
  [&_.cm-tooltip-autocomplete]:p-0.5
  [&_.cm-tooltip-autocomplete]:shadow-md
  [&_.cm-tooltip-autocomplete>ul]:rounded-sm
  [&_.cm-tooltip-autocomplete>ul>li]:flex
  [&_.cm-tooltip-autocomplete>ul>li]:min-h-5
  [&_.cm-tooltip-autocomplete>ul>li]:items-center
  [&_.cm-tooltip-autocomplete>ul>li]:rounded-sm
  [&_.cm-tooltip-autocomplete>ul>li[aria-selected=true]]:bg-sky-200/50
  [&_.cm-tooltip-autocomplete>ul>li[aria-selected=true]]:text-accent-foreground
  [&_.cm-tooltip-autocomplete>ul>li]:py-2.5
  [&_.cm-function-info-tooltip]:shadow-md
`
