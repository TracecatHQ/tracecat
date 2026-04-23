"use client"

import { EditorContent, useEditor } from "@tiptap/react"
import { StarterKit } from "@tiptap/starter-kit"
import { Redo2, Undo2 } from "lucide-react"
import * as React from "react"

import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

type PlainTextEditorProps = {
  /** Plain text content rendered by the editor. */
  value: string
  /** Called with the latest plain text whenever the editor content changes. */
  onChange?: (value: string) => void
  /** Called when Cmd/Ctrl+S is pressed inside the editor. */
  onSave?: () => void
  /** Called when the editor gains focus. */
  onFocus?: () => void
  /** Called when the editor loses focus. */
  onBlur?: () => void
  /** Placeholder rendered when the editor is empty. */
  placeholder?: string
  /** Whether the editor accepts input. */
  editable?: boolean
  /** Wrapper class name. */
  className?: string
}

type ParagraphNode = {
  type: "paragraph"
  content?: { type: "text"; text: string }[]
}

type DocNode = {
  type: "doc"
  content: ParagraphNode[]
}

const STARTER_KIT_OPTIONS = {
  blockquote: false as const,
  bold: false as const,
  bulletList: false as const,
  code: false as const,
  codeBlock: false as const,
  heading: false as const,
  horizontalRule: false as const,
  italic: false as const,
  link: false as const,
  listItem: false as const,
  listKeymap: false as const,
  orderedList: false as const,
  strike: false as const,
  underline: false as const,
  trailingNode: false as const,
}

function toParagraph(line: string): ParagraphNode {
  if (line.length === 0) {
    return { type: "paragraph" }
  }
  return { type: "paragraph", content: [{ type: "text", text: line }] }
}

function buildDoc(text: string): DocNode {
  const lines = text.length === 0 ? [""] : text.split("\n")
  return {
    type: "doc",
    content: lines.map(toParagraph),
  }
}

function countWords(text: string): number {
  const trimmed = text.trim()
  if (trimmed.length === 0) {
    return 0
  }
  return trimmed.split(/\s+/).length
}

/**
 * Minimal Tiptap editor that round-trips plain text without applying any
 * markdown semantics. Uses Document/Paragraph/Text/HardBreak only; Enter
 * creates a new paragraph that serializes to a single newline.
 */
export function PlainTextEditor({
  value,
  onChange,
  onSave,
  onFocus,
  onBlur,
  placeholder,
  editable = true,
  className,
}: PlainTextEditorProps) {
  const valueRef = React.useRef(value ?? "")
  const [editorText, setEditorText] = React.useState(value ?? "")

  const editor = useEditor({
    immediatelyRender: false,
    shouldRerenderOnTransaction: true,
    editable,
    extensions: [StarterKit.configure(STARTER_KIT_OPTIONS)],
    content: buildDoc(value ?? ""),
    editorProps: {
      attributes: {
        autocomplete: "off",
        autocorrect: "off",
        autocapitalize: "off",
        "aria-label": "Plain text editor",
        class: "plain-text-editor",
        ...(placeholder ? { "data-placeholder": placeholder } : {}),
      },
    },
    onUpdate: ({ editor }) => {
      if (!editor.isEditable) {
        return
      }
      const next = editor.getText({ blockSeparator: "\n" })
      setEditorText(next)
      if (next === valueRef.current) {
        return
      }
      valueRef.current = next
      onChange?.(next)
    },
    onFocus: () => {
      onFocus?.()
    },
    onBlur: () => {
      onBlur?.()
    },
  })

  React.useEffect(() => {
    if (!editor) {
      valueRef.current = value ?? ""
      return
    }
    const next = value ?? ""
    if (next === valueRef.current) {
      return
    }
    valueRef.current = next
    setEditorText(next)
    editor.commands.setContent(buildDoc(next), { emitUpdate: false })
  }, [editor, value])

  React.useEffect(() => {
    if (!editor) return

    const dom = editor.view.dom

    const handleKeydown = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey)) return
      if (event.key.toLowerCase() !== "s") return
      event.preventDefault()
      event.stopPropagation()
      if (editor.isEditable) {
        onSave?.()
      }
    }

    dom.addEventListener("keydown", handleKeydown)

    return () => {
      dom.removeEventListener("keydown", handleKeydown)
    }
  }, [editor, onSave])

  const wordCount = React.useMemo(() => countWords(editorText), [editorText])
  const charCount = editorText.length
  const canUndo = editable && (editor?.can().undo() ?? false)
  const canRedo = editable && (editor?.can().redo() ?? false)

  return (
    <div className={cn("flex w-full flex-col bg-background", className)}>
      <div className="flex items-center justify-between gap-2 border-b border-border/60 px-2 py-1">
        <div className="flex items-center gap-0.5">
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-7"
            disabled={!canUndo}
            onClick={() => {
              editor?.chain().focus().undo().run()
            }}
            aria-label="Undo"
            title="Undo"
          >
            <Undo2 className="size-4" />
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-7"
            disabled={!canRedo}
            onClick={() => {
              editor?.chain().focus().redo().run()
            }}
            aria-label="Redo"
            title="Redo"
          >
            <Redo2 className="size-4" />
          </Button>
        </div>
        <div className="text-xs tabular-nums text-muted-foreground">
          {wordCount} {wordCount === 1 ? "word" : "words"} · {charCount}{" "}
          {charCount === 1 ? "char" : "chars"}
        </div>
      </div>
      <EditorContent
        editor={editor}
        className={cn(
          "flex-1 px-4 py-3 text-sm leading-6 text-foreground",
          "[&_.ProseMirror]:min-h-[12rem] [&_.ProseMirror]:outline-none",
          "[&_.ProseMirror]:whitespace-pre-wrap [&_.ProseMirror]:break-words",
          "[&_.ProseMirror_p]:my-0"
        )}
      />
    </div>
  )
}
