"use client"

/**
 * Expression Input - A single-line input that looks like shadcn/ui Input but has
 * template expression pill functionality backed by CodeMirror
 */
import React, { useCallback, useMemo } from "react"
import { ActionRead } from "@/client"
import { useWorkflow } from "@/providers/workflow"
import { useWorkspace } from "@/providers/workspace"
import { closeBrackets } from "@codemirror/autocomplete"
import { history } from "@codemirror/commands"
import { bracketMatching, indentUnit } from "@codemirror/language"
import { linter, type Diagnostic } from "@codemirror/lint"
import { EditorState } from "@codemirror/state"
import { EditorView, placeholder, type ViewUpdate } from "@codemirror/view"
import CodeMirror from "@uiw/react-codemirror"
import { useFormContext } from "react-hook-form"

import { createTemplateRegex } from "@/lib/expressions"
import { cn } from "@/lib/utils"
import { Input } from "@/components/ui/input"
import {
  createAtKeyCompletion,
  createAutocomplete,
  createBlurHandler,
  createCoreKeymap,
  createExitEditModeKeyHandler,
  createExpressionNodeHover,
  createPillClickHandler,
  createPillDeleteKeymap,
  createTemplatePillPlugin,
  editingRangeField,
  EDITOR_STYLE,
  templatePillTheme,
} from "@/components/editor/codemirror/common"
import { ExpressionErrorBoundary } from "@/components/error-boundaries"

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
  defaultHeight?: "input" | "text-area"
}

// Simple fallback input component for when ExpressionInput fails
function SimpleFallbackInput({
  value,
  onChange,
  placeholder,
  className,
  disabled,
  defaultHeight,
}: ExpressionInputProps) {
  const safeValue =
    typeof value === "string" ? value : JSON.stringify(value || "")

  return (
    <div className={cn("relative", className)}>
      <Input
        value={safeValue}
        onChange={(e) => onChange?.(e.target.value)}
        placeholder={placeholder}
        disabled={disabled}
        className={cn(
          "text-xs",
          defaultHeight === "text-area" && "min-h-[240px]"
        )}
      />
    </div>
  )
}

// Core ExpressionInput implementation
function ExpressionInputCore({
  value = "",
  onChange,
  placeholder: placeholderText = "Type @ to begin an expression...",
  className,
  disabled = false,
  defaultHeight = "input",
}: ExpressionInputProps) {
  const { workspaceId } = useWorkspace()
  const { workflow } = useWorkflow()
  const methods = useFormContext()
  const actions = workflow?.actions || ({} as Record<string, ActionRead>)
  const forEach = useMemo(() => methods.watch("for_each"), [methods])

  // Safe value conversion with error handling
  const safeValue = useMemo(() => {
    try {
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

      return convertedValue
    } catch (error) {
      console.error(
        "ExpressionInput: Failed to convert value to string:",
        error
      )
      return "[Conversion Error]"
    }
  }, [value])

  const extensions = useMemo(() => {
    const templatePillPluginInstance = createTemplatePillPlugin(workspaceId)

    return [
      // Keymaps
      createPillDeleteKeymap(), // This must be first to ensure that the delete key is handled before the core keymap
      createCoreKeymap(),
      createAtKeyCompletion(),
      createExitEditModeKeyHandler(),

      // Core setup
      history(),
      EditorState.allowMultipleSelections.of(true),
      indentUnit.of("  "),

      // Linting
      linter(expressionLinter),

      // Features
      bracketMatching(),
      closeBrackets(),
      createAutocomplete({
        workspaceId,
        actions,
        forEach,
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
        ".cm-line": {
          padding: "0px",
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
    </div>
  )
}

// Main ExpressionInput component with error boundary
export function ExpressionInput(props: ExpressionInputProps) {
  const fallbackInput = <SimpleFallbackInput {...props} />

  return (
    <ExpressionErrorBoundary
      fieldName="expression"
      value={
        typeof props.value === "string"
          ? props.value
          : String(props.value || "")
      }
      onChange={props.onChange}
      fallbackInput={fallbackInput}
    >
      <ExpressionInputCore {...props} />
    </ExpressionErrorBoundary>
  )
}
