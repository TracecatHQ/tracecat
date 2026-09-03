"use client"

import * as React from "react"

import { SimpleEditor } from "@/components/tiptap-templates/simple/simple-editor"
import { useCaseImageUpload } from "@/lib/cases/use-case-image-upload"
import { cn } from "@/lib/utils"

import "./editor.css"

interface CaseDescriptionEditorProps {
  initialContent?: string
  onChange?: (value: string) => void
  className?: string
  onBlur?: () => void
  toolbarStatus?: React.ReactNode
  autoFocus?: boolean
  /** Case and workspace to attach pasted/dropped images to; omit to disable image uploads. */
  imageTarget?: {
    caseId: string
    workspaceId: string
  }
}

export function CaseDescriptionEditor({
  initialContent,
  onChange,
  className,
  onBlur,
  toolbarStatus,
  autoFocus = false,
  imageTarget,
}: CaseDescriptionEditorProps) {
  const [value, setValue] = React.useState(initialContent ?? "")
  const [isEditorActive, setIsEditorActive] = React.useState(false)
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
        setIsEditorActive(false)
        onBlur?.()
      }
    },
    [onBlur]
  )

  const handleContainerFocus = React.useCallback(() => {
    setIsEditorActive(true)
  }, [])

  const imagesEnabled = Boolean(imageTarget)
  const { uploadImage } = useCaseImageUpload(
    imageTarget?.caseId ?? "",
    imageTarget?.workspaceId ?? ""
  )
  const handleImageUpload = React.useCallback(
    async (file: File) => (await uploadImage(file)).src,
    [uploadImage]
  )

  return (
    <div
      ref={containerRef}
      className={cn("mx-0", className)}
      onFocusCapture={handleContainerFocus}
      onBlur={handleContainerBlur}
    >
      <SimpleEditor
        value={value}
        onChange={handleChange}
        onShortcutFallback={onBlur}
        showToolbar={isEditorActive}
        preserveToolbarSpace
        toolbarStatus={toolbarStatus}
        renderMermaidWhenBlurred
        placeholder="Describe the case..."
        className="case-description-editor"
        autoFocus={autoFocus}
        enableImages={imagesEnabled}
        imageWorkspaceId={imageTarget?.workspaceId ?? null}
        onImageUpload={imagesEnabled ? handleImageUpload : undefined}
      />
    </div>
  )
}

export function CaseCommentViewer({
  content,
  className,
  workspaceId,
}: {
  content: string
  className?: string
  /** Workspace that owns the case; required to render inline attachment images. */
  workspaceId?: string
}) {
  return (
    <div className={cn("m-0 p-0 text-base", className)}>
      <SimpleEditor
        value={content}
        editable={false}
        showToolbar={false}
        className="case-comment-viewer"
        enableImages={Boolean(workspaceId)}
        imageWorkspaceId={workspaceId ?? null}
      />
    </div>
  )
}
