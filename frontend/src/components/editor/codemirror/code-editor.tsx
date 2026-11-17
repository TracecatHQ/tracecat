"use client"

import { json } from "@codemirror/lang-json"
import { python } from "@codemirror/lang-python"
import { yaml } from "@codemirror/lang-yaml"
import type { Extension } from "@codemirror/state"
import { EditorView } from "@codemirror/view"
import ReactCodeMirror from "@uiw/react-codemirror"
import { useTheme } from "next-themes"
import { useMemo } from "react"

import { cn } from "@/lib/utils"

interface CodeEditorProps {
  value: string
  onChange?: (value: string) => void
  language?: string
  readOnly?: boolean
  wrapLongLines?: boolean
  placeholder?: string
  extensions?: Extension[]
  className?: string
  /** Called once with the editor view, for callers that drive it directly. */
  onCreateEditor?: (view: EditorView) => void
}

const NO_EXTENSIONS: Extension[] = []

function getLanguageExtension(language: string): Extension | null {
  switch (language) {
    case "python":
      return python()
    case "json":
      return json()
    case "yaml":
      return yaml()
    case "text":
    default:
      return null
  }
}

export function CodeEditor({
  value,
  onChange,
  language = "python",
  readOnly = false,
  wrapLongLines = false,
  placeholder,
  extensions: customExtensions = NO_EXTENSIONS,
  className,
  onCreateEditor,
}: CodeEditorProps) {
  const { resolvedTheme } = useTheme()
  const codeMirrorTheme = resolvedTheme === "dark" ? "dark" : "light"
  // A new array reconfigures the editor, so keep it stable for callers that
  // pass stable extensions.
  const extensions = useMemo(() => {
    const languageExtension = getLanguageExtension(language)
    return [
      ...(languageExtension ? [languageExtension] : []),
      ...(wrapLongLines ? [EditorView.lineWrapping] : []),
      ...customExtensions,
    ]
  }, [language, wrapLongLines, customExtensions])

  return (
    <ReactCodeMirror
      value={value}
      onChange={onChange}
      extensions={extensions}
      theme={codeMirrorTheme}
      readOnly={readOnly}
      placeholder={placeholder}
      onCreateEditor={onCreateEditor}
      className={cn(
        // Ensure the editor and all its tooltips/autocomplete popups are fully rounded and do not stick out
        "flex max-w-full min-w-0 flex-col rounded-md text-xs focus-visible:outline-none",
        // Editor container. Grows to any min-height set by the caller.
        "[&_.cm-editor]:min-w-0 [&_.cm-editor]:grow [&_.cm-editor]:rounded-md [&_.cm-editor]:border [&_.cm-focused]:outline-none",
        // Scroller fills the editor so the gutter and horizontal scrollbar
        // reach its bottom edge.
        "[&_.cm-scroller]:min-w-0 [&_.cm-scroller]:grow [&_.cm-scroller]:overflow-auto [&_.cm-scroller]:rounded-md [&_.cm-editor_.cm-scroller]:!items-stretch [&_.cm-editor_.cm-gutters]:!h-auto",
        wrapLongLines
          ? "[&_.cm-content]:min-w-0 [&_.cm-content]:w-full [&_.cm-line]:break-words [&_.cm-lineWrapping]:break-words [&_.cm-lineWrapping]:whitespace-pre-wrap"
          : "[&_.cm-content]:w-max",
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
        "[&_.cm-tooltip-autocomplete>ul>li]:py-2.5",
        className
      )}
    />
  )
}
