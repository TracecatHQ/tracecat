"use client"

import * as React from "react"
import { SimpleEditor } from "@/components/tiptap-templates/simple/simple-editor"
import { cn } from "@/lib/utils"

interface CaseDescriptionEditorProps {
  initialContent?: string
  onChange?: (value: string) => void
  className?: string
  onBlur?: () => void
}

export function CaseDescriptionEditor({
  initialContent,
  onChange,
  className,
  onBlur,
}: CaseDescriptionEditorProps) {
  const [content, setContent] = React.useState(initialContent ?? "")

  React.useEffect(() => {
    setContent(initialContent ?? "")
  }, [initialContent])

  const handleChange = React.useCallback(
    (updated: string) => {
      setContent(updated)
      onChange?.(updated)
    },
    [onChange]
  )

  return (
    <div className="py-2">
      <SimpleEditor
        value={content}
        onChange={handleChange}
        onBlur={onBlur}
        className={cn("h-full", className)}
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
    <SimpleEditor
      value={content}
      editable={false}
      className={cn("border-0 bg-transparent text-sm leading-6", className)}
    />
  )
}
