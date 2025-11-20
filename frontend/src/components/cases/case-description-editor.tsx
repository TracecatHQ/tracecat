"use client"

import * as React from "react"

import { SimpleEditor } from "@/components/tiptap-templates/simple/simple-editor"
import { cn } from "@/lib/utils"

import "./editor.css"

interface CaseDescriptionEditorProps {
  initialContent?: string
  onChange?: (value: string) => void
  className?: string
  onBlur?: () => void
  toolbarStatus?: React.ReactNode
}

export function CaseDescriptionEditor({
  initialContent,
  onChange,
  className,
  onBlur,
  toolbarStatus,
}: CaseDescriptionEditorProps) {
  const [value, setValue] = React.useState(initialContent ?? "")
  const containerRef = React.useRef<HTMLDivElement>(null)

  React.useEffect(() => {
    setValue(initialContent ?? "")
  }, [initialContent])

  const handleChange = React.useCallback(
    (nextValue: string) => {
      setValue(nextValue)
      onChange?.(nextValue)
    },
    [onChange]
  )

  // Only fire onBlur when focus leaves the entire editor area (content + toolbar)
  const handleContainerBlur = React.useCallback(
    (event: React.FocusEvent<HTMLDivElement>) => {
      const container = containerRef.current
      const nextTarget = event.relatedTarget as Node | null

      // If next focus target is not inside the editor container (or is null), treat as external blur.
      if (!container || !nextTarget || !container.contains(nextTarget)) {
        onBlur?.()
      }
    },
    [onBlur]
  )

  return (
    <div
      ref={containerRef}
      className={cn("mx-0", className)}
      onBlur={handleContainerBlur}
    >
      <SimpleEditor
        value={value}
        onChange={handleChange}
        onShortcutFallback={onBlur}
        toolbarStatus={toolbarStatus}
        placeholder="Describe the case..."
        className="case-description-editor"
      />
    </div>
  )
}

export function CaseCommentViewer({
  content,
  className,
}: {
  content: string
  className?: string
}) {
  return (
    <div className={cn("m-0 p-0 text-base", className)}>
      <SimpleEditor
        value={content}
        editable={false}
        showToolbar={false}
        className="case-comment-viewer"
      />
    </div>
  )
}
