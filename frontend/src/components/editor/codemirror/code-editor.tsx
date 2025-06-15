"use client"

import { python } from "@codemirror/lang-python"
import ReactCodeMirror from "@uiw/react-codemirror"

import { cn } from "@/lib/utils"

interface CodeEditorProps {
  value: string
  onChange?: (value: string) => void
  language?: string
  readOnly?: boolean
  className?: string
}
export const getLanguageExtension = (language: string) => {
  switch (language) {
    case "python":
      return python()
    default:
      return python()
  }
}

export function CodeEditor({
  value,
  onChange,
  language = "python",
  readOnly = false,
  className,
}: CodeEditorProps) {
  return (
    <ReactCodeMirror
      value={value}
      onChange={onChange}
      extensions={[getLanguageExtension(language)]}
      readOnly={readOnly}
      className={cn(
        // Ensure the editor and all its tooltips/autocomplete popups are fully rounded and do not stick out
        "rounded-md text-xs focus-visible:outline-none",
        // Editor container
        "[&_.cm-editor]:rounded-md [&_.cm-editor]:border [&_.cm-focused]:outline-none",
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
        "[&_.cm-tooltip-autocomplete>ul>li]:py-2.5",
        className
      )}
    />
  )
}
