"use client"

/**
 * Expression Input - A single-line input that looks like shadcn/ui Input but has
 * template expression pill functionality backed by CodeMirror
 */
import React, { useCallback, useMemo, useState } from "react"
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
import { bracketMatching, indentUnit } from "@codemirror/language"
import { linter, type Diagnostic } from "@codemirror/lint"
import { EditorState } from "@codemirror/state"
import {
  EditorView,
  keymap,
  placeholder,
  type ViewUpdate,
} from "@codemirror/view"
import CodeMirror from "@uiw/react-codemirror"
import { AlertTriangle } from "lucide-react"
import { useFormContext } from "react-hook-form"

import { cn } from "@/lib/utils"
import {
  createActionCompletion,
  createAtKeyCompletion,
  createBlurHandler,
  createExitEditModeKeyHandler,
  createExpressionNodeHover,
  createFunctionCompletion,
  createMentionCompletion,
  createPillClickHandler,
  createTemplatePillPlugin,
  createTemplateRegex,
  createVarCompletion,
  editingRangeField,
  EDITOR_STYLE,
  enhancedCursorLeft,
  enhancedCursorRight,
  templatePillTheme,
} from "@/components/editor/codemirror/common"

// Single-line expression linter
function expressionLinter(view: EditorView): Diagnostic[] {
  const diagnostics: Diagnostic[] = []
  const content = view.state.doc.toString()

  if (!content.trim()) {
    return diagnostics
  }

  // Check for basic syntax issues in template expressions
  const regex = createTemplateRegex()
  let match: RegExpExecArray | null

  while ((match = regex.exec(content)) !== null) {
    const innerContent = match[1].trim()
    const start = match.index
    const end = start + match[0].length

    // Basic validation
    if (
      innerContent.includes("..") ||
      innerContent.endsWith(".") ||
      innerContent.includes("undefined") ||
      innerContent === ""
    ) {
      diagnostics.push({
        from: start,
        to: end,
        severity: "error",
        message: "Invalid expression syntax",
        source: "expression",
      })
    }
  }

  return diagnostics
}

export interface ExpressionInputProps {
  value?: string | unknown
  onChange?: (value: string) => void
  placeholder?: string
  className?: string
  disabled?: boolean
  showTypeWarnings?: boolean
  onTypeConversion?: (originalValue: unknown, convertedValue: string) => void
  defaultHeight?: "input" | "text-area"
}

export function ExpressionInput({
  value = "",
  onChange,
  placeholder: placeholderText = "Enter expression...",
  className,
  disabled = false,
  showTypeWarnings = true,
  onTypeConversion,
  defaultHeight = "input",
}: ExpressionInputProps) {
  const { workspaceId } = useWorkspace()
  const { workflow } = useWorkflow()
  const methods = useFormContext()
  const [typeConversionWarning, setTypeConversionWarning] = useState<{
    originalType: string
    originalValue: unknown
    convertedValue: string
  } | null>(null)
  const actions = workflow?.actions || []
  const forEach = useMemo(
    () => methods.watch("control_flow.for_each"),
    [methods]
  )

  // Safe value conversion with error handling
  const safeValue = useMemo(() => {
    try {
      // Reset warning state
      setTypeConversionWarning(null)

      // Handle null/undefined
      if (value == null) {
        return ""
      }

      // Handle string values directly
      if (typeof value === "string") {
        return value
      }

      // Handle non-string values with conversion
      let convertedValue: string
      const originalType = typeof value

      if (typeof value === "number" || typeof value === "boolean") {
        convertedValue = String(value)
      } else if (typeof value === "object") {
        // Try to stringify as JSON with formatting
        try {
          convertedValue = JSON.stringify(value, null, 2)
        } catch (jsonError) {
          // Fallback for objects that can't be JSON stringified
          convertedValue = String(value)
        }
      } else {
        // Fallback for other types
        convertedValue = String(value)
      }

      // Set type conversion warning
      if (showTypeWarnings && originalType !== "string") {
        setTypeConversionWarning({
          originalType,
          originalValue: value,
          convertedValue,
        })

        // Call optional callback
        onTypeConversion?.(value, convertedValue)

        // Log warning for development
        console.warn(
          `ExpressionInput: Non-string value of type "${originalType}" was converted to string:`,
          { original: value, converted: convertedValue }
        )
      }

      return convertedValue
    } catch (error) {
      console.error(
        "ExpressionInput: Failed to convert value to string:",
        error
      )
      setTypeConversionWarning({
        originalType: typeof value,
        originalValue: value,
        convertedValue: "[Conversion Error]",
      })
      return "[Conversion Error]"
    }
  }, [value, showTypeWarnings, onTypeConversion])

  const extensions = useMemo(() => {
    const templatePillPluginInstance = createTemplatePillPlugin(workspaceId)

    return [
      // Core setup
      history(),
      EditorState.allowMultipleSelections.of(true),
      indentUnit.of("  "),

      // Linting
      linter(expressionLinter),

      // Keymaps
      createExitEditModeKeyHandler(),
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

      // Features
      bracketMatching(),
      closeBrackets(),
      autocompletion({
        override: [
          createMentionCompletion(),
          createFunctionCompletion(workspaceId),
          createActionCompletion(Object.values(actions).map((a) => a)),
          createVarCompletion(forEach),
        ],
      }),

      // Placeholder
      placeholder(placeholderText),

      // Custom plugins
      editingRangeField,
      templatePillPluginInstance,
      createExpressionNodeHover(workspaceId),

      // Event handlers
      EditorView.domEventHandlers({
        mousedown: createPillClickHandler(),
        blur: createBlurHandler(),
      }),

      // Theme
      templatePillTheme,

      // Input-specific styling to match shadcn Input
      EditorView.theme({
        "&": {
          fontSize: "14px",
        },
        ".cm-content": {
          padding: "8px 12px",
          lineHeight: "20px",
          // text-xs
          fontSize: "12px",
          caretColor: "hsl(var(--foreground))",
          ...(defaultHeight === "text-area" && {
            minHeight: "240px",
          }),
        },
        ".cm-scroller": {
          fontFamily: "inherit",
          ...(defaultHeight === "text-area" && {
            minHeight: "240px",
          }),
        },
        ".cm-focused": {
          outline: "2px solid transparent",
          outlineOffset: "2px",
          borderColor: "hsl(var(--ring))",
          boxShadow: "0 0 0 2px hsl(var(--ring))",
        },
        ".cm-editor": {
          backgroundColor: "hsl(var(--background))",
          color: "hsl(var(--foreground))",
          fontSize: "14px",
          transition: "all 0.2s",
        },
        ".cm-editor.cm-focused": {
          borderColor: "hsl(var(--ring))",
          boxShadow: "0 0 0 2px hsl(var(--ring))",
        },
        "&.cm-disabled": {
          cursor: "not-allowed",
          opacity: "0.5",
        },
        "&.cm-disabled .cm-content": {
          color: "hsl(var(--muted-foreground))",
        },
        ".cm-placeholder": {
          color: "hsl(var(--muted-foreground))",
          fontStyle: "normal",
        },
        // Error styling
        ".cm-diagnostic-error": {
          borderBottom: "2px wavy hsl(var(--destructive))",
        },
        ".cm-lint-marker-error": {
          backgroundColor: "hsl(var(--destructive))",
          borderRadius: "50%",
          width: "0.6em",
          height: "0.6em",
        },
      }),
    ]
  }, [workspaceId, actions, placeholderText, forEach, defaultHeight])

  const handleChange = useCallback(
    (val: string, viewUpdate: ViewUpdate) => {
      onChange?.(val)
    },
    [onChange]
  )

  // Apply variant styles from inputVariants

  return (
    <div className={cn("relative", className)}>
      <div className="no-scrollbar max-h-[800px] overflow-auto rounded-md border-[0.5px] border-border shadow-sm">
        <CodeMirror
          value={safeValue}
          height="auto"
          extensions={extensions}
          onChange={handleChange}
          editable={!disabled}
          basicSetup={false} // We're handling setup manually
          className={EDITOR_STYLE}
        />
      </div>

      {/* Type conversion warning */}
      {typeConversionWarning && (
        <div className="mt-2 flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm">
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-amber-600" />
          <div className="flex-1">
            <div className="font-medium text-amber-800">
              Data type converted
            </div>
            <div className="mt-1 text-amber-700">
              Received {typeConversionWarning.originalType} value, automatically
              converted to string for editing.
            </div>
            {(typeConversionWarning.originalType === "object" ||
              typeConversionWarning.originalType === "boolean" ||
              typeConversionWarning.originalType === "number") && (
              <div className="mt-2 text-xs text-amber-600">
                <div className="font-medium">Original value:</div>
                <div className="mt-1 max-w-md overflow-auto rounded bg-amber-100 p-1 font-mono">
                  {typeConversionWarning.originalType === "object"
                    ? JSON.stringify(
                        typeConversionWarning.originalValue,
                        null,
                        2
                      )
                    : String(typeConversionWarning.originalValue)}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
