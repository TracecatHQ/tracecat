"use client"

import type { Editor } from "@tiptap/react"
import * as React from "react"
import { SimpleEditor } from "@/components/tiptap-templates/simple/simple-editor"
import { useCaseImageUpload } from "@/lib/cases/use-case-image-upload"
import { cn } from "@/lib/utils"

import "./editor.css"

interface CaseCommentEditorProps {
  value: string
  onChange: (value: string) => void
  caseId: string
  workspaceId: string
  placeholder: string
  mode?: "default" | "inline"
  autoFocus?: boolean
  onBlur?: () => void
  onFocus?: () => void
  onUploadingChange?: (isUploading: boolean) => void
  onEditorReady?: (editor: Editor | null) => void
}

/**
 * TipTap Markdown editor styled to occupy the existing comment-composer shell.
 */
export function CaseCommentEditor({
  value,
  onChange,
  caseId,
  workspaceId,
  placeholder,
  mode = "default",
  autoFocus = false,
  onBlur,
  onFocus,
  onUploadingChange,
  onEditorReady,
}: CaseCommentEditorProps) {
  const containerRef = React.useRef<HTMLDivElement>(null)
  const [uploadingCount, setUploadingCount] = React.useState(0)
  const { uploadImage } = useCaseImageUpload(caseId, workspaceId)

  React.useEffect(() => {
    onUploadingChange?.(uploadingCount > 0)
  }, [onUploadingChange, uploadingCount])

  const handleImageUpload = React.useCallback(
    async (file: File) => {
      setUploadingCount((count) => count + 1)
      try {
        return (await uploadImage(file)).src
      } finally {
        setUploadingCount((count) => Math.max(0, count - 1))
      }
    },
    [uploadImage]
  )

  const handleContainerBlur = React.useCallback(
    (event: React.FocusEvent<HTMLDivElement>) => {
      const nextTarget = event.relatedTarget as Node | null
      if (
        !containerRef.current ||
        !nextTarget ||
        !containerRef.current.contains(nextTarget)
      ) {
        onBlur?.()
      }
    },
    [onBlur]
  )

  const handleContainerFocus = React.useCallback(() => {
    onFocus?.()
  }, [onFocus])

  return (
    <div
      ref={containerRef}
      className={cn(
        "case-comment-editor",
        mode === "inline" && "case-comment-editor--inline"
      )}
      onBlur={handleContainerBlur}
      onFocusCapture={handleContainerFocus}
    >
      <SimpleEditor
        value={value}
        onChange={onChange}
        showToolbar={false}
        placeholder={placeholder}
        className="case-comment-editor__tiptap"
        autoFocus={autoFocus}
        enableImages
        imageWorkspaceId={workspaceId}
        onImageUpload={handleImageUpload}
        onEditorReady={onEditorReady}
      />
    </div>
  )
}
