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

import { cn } from "@/lib/utils"
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
  createTemplateRegex,
  editingRangeField,
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
  value?: string
  onChange?: (value: string) => void
  placeholder?: string
  className?: string
  disabled?: boolean
  variant?: "default" | "flat"
}

export function ExpressionInput({
  value = "",
  onChange,
  placeholder: placeholderText = "Enter expression...",
  className,
  disabled = false,
  variant = "default",
}: ExpressionInputProps) {
  const { workspaceId } = useWorkspace()
  const { workflow } = useWorkflow()
  const [editorView, setEditorView] = useState<EditorView | null>(null)
  const actions = workflow?.actions || []

  const extensions = useMemo(() => {
    const templatePillPluginInstance = createTemplatePillPlugin(workspaceId)

    return [
      // Core setup
      history(),
      EditorState.allowMultipleSelections.of(true),
      indentUnit.of("  "),

      // Single-line configuration
      EditorView.lineWrapping,
      EditorState.transactionFilter.of((tr) => {
        if (tr.newDoc.lines > 1) {
          const singleLineText = tr.newDoc.toString().replace(/\n/g, " ")
          return {
            changes: {
              from: 0,
              to: tr.startState.doc.length,
              insert: singleLineText,
            },
          }
        }
        return tr
      }),

      // Linting
      linter(expressionLinter),

      // Keymaps
      keymap.of([
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
          run: () => true, // Prevent newlines
        },
        ...closeBracketsKeymap,
        ...standardKeymap,
        ...historyKeymap,
        ...completionKeymap,
        indentWithTab,
      ]),
      createAtKeyCompletion(),
      createEscapeKeyHandler(),

      // Features
      bracketMatching(),
      closeBrackets(),
      autocompletion({
        override: [
          createMentionCompletion(),
          createFunctionCompletion(workspaceId),
          createActionCompletion(Object.values(actions).map((a) => a)),
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
          minHeight: "36px",
          lineHeight: "20px",
          caretColor: "hsl(var(--foreground))",
        },
        ".cm-focused": {
          outline: "2px solid transparent",
          outlineOffset: "2px",
          borderColor: "hsl(var(--ring))",
          boxShadow: "0 0 0 2px hsl(var(--ring))",
        },
        ".cm-editor": {
          borderRadius: "calc(var(--radius) - 2px)",
          border: "1px solid hsl(var(--border))",
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
        ".cm-scroller": {
          fontFamily: "inherit",
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
  }, [workspaceId, actions, placeholderText])

  const handleChange = useCallback(
    (val: string, viewUpdate: ViewUpdate) => {
      // Ensure single line
      const singleLineValue = val.replace(/\n/g, " ")
      if (singleLineValue !== val) {
        onChange?.(singleLineValue)
      } else {
        onChange?.(val)
      }
    },
    [onChange]
  )

  // Apply variant styles from inputVariants

  return (
    <div className={cn("relative", className)}>
      <div className="no-scrollbar max-h-[800px] overflow-auto rounded-md border">
        <CodeMirror
          value={value}
          height="auto"
          extensions={extensions}
          onChange={handleChange}
          editable={!disabled}
          onCreateEditor={(view) => setEditorView(view)}
          basicSetup={false} // We're handling setup manually
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
    </div>
  )
}
