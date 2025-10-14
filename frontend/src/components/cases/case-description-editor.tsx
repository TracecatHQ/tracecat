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
}

export function CaseDescriptionEditor({
  initialContent,
  onChange,
  className,
  onBlur,
}: CaseDescriptionEditorProps) {
  const [value, setValue] = React.useState(initialContent ?? "")

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

  return (
    <div className={cn("mx-0", className)}>
      <SimpleEditor
        value={value}
        onChange={handleChange}
        onBlur={onBlur}
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
