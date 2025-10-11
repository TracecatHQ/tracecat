"use client"

import { CodeBlockLowlight } from "@tiptap/extension-code-block-lowlight"
import { Placeholder } from "@tiptap/extension-placeholder"
import { EditorContent, useEditor } from "@tiptap/react"
import { StarterKit } from "@tiptap/starter-kit"
import { common, createLowlight } from "lowlight"
import {
  Bold,
  Braces,
  Code2,
  Heading1,
  Heading2,
  Heading3,
  Italic,
  List,
  ListOrdered,
  Quote,
  Redo,
  Strikethrough,
  Undo,
} from "lucide-react"
import * as React from "react"
import { Markdown } from "tiptap-markdown-3"

import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { cn } from "@/lib/utils"

import "@/components/tiptap-templates/simple/simple-editor.scss"

const lowlight = createLowlight(common)

export interface SimpleEditorProps {
  value?: string
  onChange?: (markdown: string) => void
  editable?: boolean
  placeholder?: string
  className?: string
  onBlur?: () => void
}

const DEFAULT_PLACEHOLDER =
  "Click to start writing. Use markdown syntax for quick formatting."

const TOOLBAR_BUTTON_CLASSES =
  "h-8 w-8 rounded-sm bg-transparent text-foreground transition-colors duration-150 hover:bg-primary/10 hover:text-primary focus-visible:ring-0 disabled:text-muted-foreground/50 data-[active=true]:bg-primary/10 data-[active=true]:text-primary"

export function SimpleEditor({
  value,
  onChange,
  editable = true,
  placeholder = DEFAULT_PLACEHOLDER,
  className,
  onBlur,
}: SimpleEditorProps) {
  const onChangeRef = React.useRef(onChange)
  const lastSyncedMarkdown = React.useRef(value ?? "")

  React.useEffect(() => {
    onChangeRef.current = onChange
  }, [onChange])

  const editor = useEditor({
    content: value ?? "",
    immediatelyRender: false,
    editable,
    shouldRerenderOnTransaction: false,
    editorProps: {
      attributes: {
        class: cn(
          "simple-editor prose prose-sm max-w-none focus:outline-none",
          editable ? "cursor-text" : "cursor-default"
        ),
      },
    },
    extensions: [
      StarterKit.configure({
        codeBlock: false,
      }),
      CodeBlockLowlight.configure({
        lowlight,
      }),
      Placeholder.configure({
        placeholder,
        showOnlyCurrent: false,
        showOnlyWhenEditable: true,
      }),
      Markdown.configure({
        html: false,
        tightLists: true,
        tightListClass: "tight",
        linkify: true,
        breaks: true,
        transformPastedText: true,
        transformCopiedText: false,
      }),
    ],
    onCreate({ editor }) {
      // tiptap-markdown-3 attaches 'markdown' to storage, but types don't include it
      const markdownApi = (
        editor.storage as unknown as { markdown: { getMarkdown: () => string } }
      ).markdown
      lastSyncedMarkdown.current = markdownApi.getMarkdown()
    },
    onUpdate({ editor }) {
      const markdownApi = (
        editor.storage as unknown as { markdown: { getMarkdown: () => string } }
      ).markdown
      const markdown = markdownApi.getMarkdown()
      lastSyncedMarkdown.current = markdown
      onChangeRef.current?.(markdown)
    },
  })

  React.useEffect(() => {
    if (!editor) return
    if (editor.isEditable === editable) return
    editor.setEditable(editable)
  }, [editable, editor])

  React.useEffect(() => {
    if (!editor) return
    const incoming = value ?? ""
    if (incoming === lastSyncedMarkdown.current) return
    editor.commands.setContent(incoming, { emitUpdate: false })
    lastSyncedMarkdown.current = incoming
  }, [value, editor])

  const handleBlur = React.useCallback(() => {
    onBlur?.()
  }, [onBlur])

  const handleCommand = React.useCallback(
    (command: () => boolean) => {
      if (!editor) return
      command()
    },
    [editor]
  )

  const createToggleHandler = React.useCallback(
    (command: () => boolean) =>
      (event: React.MouseEvent<HTMLButtonElement>) => {
        event.preventDefault()
        event.stopPropagation()
        handleCommand(command)
      },
    [handleCommand]
  )

  if (!editor) {
    return null
  }

  const headingLevels = [
    { level: 0, label: "Paragraph", icon: null },
    { level: 1, label: "Heading 1", icon: Heading1 },
    { level: 2, label: "Heading 2", icon: Heading2 },
    { level: 3, label: "Heading 3", icon: Heading3 },
  ] as const

  const activeHeading = headingLevels.find((item) =>
    item.level === 0
      ? editor.isActive("paragraph")
      : editor.isActive("heading", { level: item.level })
  )

  const showToolbar = editable

  return (
    <div className={cn("simple-editor-wrapper bg-card", className)}>
      {showToolbar && (
        <div className="flex flex-wrap items-center gap-1 border-b border-border bg-transparent px-2 py-1.5">
          <Button
            type="button"
            size="icon"
            variant="ghost"
            className={TOOLBAR_BUTTON_CLASSES}
            data-active={editor.isActive("bold")}
            disabled={!editor.can().chain().focus().toggleBold().run()}
            onMouseDown={(event) => event.preventDefault()}
            onClick={createToggleHandler(() =>
              editor.chain().focus().toggleBold().run()
            )}
          >
            <Bold className="h-4 w-4" />
          </Button>
          <Button
            type="button"
            size="icon"
            variant="ghost"
            className={TOOLBAR_BUTTON_CLASSES}
            data-active={editor.isActive("italic")}
            disabled={!editor.can().chain().focus().toggleItalic().run()}
            onMouseDown={(event) => event.preventDefault()}
            onClick={createToggleHandler(() =>
              editor.chain().focus().toggleItalic().run()
            )}
          >
            <Italic className="h-4 w-4" />
          </Button>
          <Button
            type="button"
            size="icon"
            variant="ghost"
            className={TOOLBAR_BUTTON_CLASSES}
            data-active={editor.isActive("strike")}
            disabled={!editor.can().chain().focus().toggleStrike().run()}
            onMouseDown={(event) => event.preventDefault()}
            onClick={createToggleHandler(() =>
              editor.chain().focus().toggleStrike().run()
            )}
          >
            <Strikethrough className="h-4 w-4" />
          </Button>
          <Button
            type="button"
            size="icon"
            variant="ghost"
            className={TOOLBAR_BUTTON_CLASSES}
            data-active={editor.isActive("code")}
            disabled={!editor.can().chain().focus().toggleCode().run()}
            onMouseDown={(event) => event.preventDefault()}
            onClick={createToggleHandler(() =>
              editor.chain().focus().toggleCode().run()
            )}
          >
            <Code2 className="h-4 w-4" />
          </Button>

          <div className="mx-2 h-6 w-px bg-border" />

          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="h-8 gap-2 bg-transparent text-foreground hover:bg-primary/10 hover:text-primary"
              >
                {activeHeading?.icon ? (
                  <activeHeading.icon className="h-4 w-4" />
                ) : (
                  <Heading1 className="h-4 w-4" />
                )}
                <span className="text-xs font-medium">
                  {activeHeading?.label ?? "Paragraph"}
                </span>
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start">
              {headingLevels.map((item) => (
                <DropdownMenuItem
                  key={item.label}
                  onMouseDown={(event) => event.preventDefault()}
                  onSelect={(event) => {
                    event.preventDefault()
                    if (item.level === 0) {
                      handleCommand(() =>
                        editor.chain().focus().setParagraph().run()
                      )
                    } else {
                      handleCommand(() =>
                        editor
                          .chain()
                          .focus()
                          .setHeading({ level: item.level })
                          .run()
                      )
                    }
                  }}
                >
                  <div className="flex items-center gap-2 text-sm">
                    {item.icon && <item.icon className="h-4 w-4" />}
                    <span>{item.label}</span>
                  </div>
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>

          <Button
            type="button"
            size="icon"
            variant="ghost"
            className={TOOLBAR_BUTTON_CLASSES}
            data-active={editor.isActive("bulletList")}
            onMouseDown={(event) => event.preventDefault()}
            onClick={createToggleHandler(() =>
              editor.chain().focus().toggleBulletList().run()
            )}
          >
            <List className="h-4 w-4" />
          </Button>
          <Button
            type="button"
            size="icon"
            variant="ghost"
            className={TOOLBAR_BUTTON_CLASSES}
            data-active={editor.isActive("orderedList")}
            onMouseDown={(event) => event.preventDefault()}
            onClick={createToggleHandler(() =>
              editor.chain().focus().toggleOrderedList().run()
            )}
          >
            <ListOrdered className="h-4 w-4" />
          </Button>

          <div className="mx-2 h-6 w-px bg-border" />

          <Button
            type="button"
            size="icon"
            variant="ghost"
            className={TOOLBAR_BUTTON_CLASSES}
            data-active={editor.isActive("blockquote")}
            onMouseDown={(event) => event.preventDefault()}
            onClick={createToggleHandler(() =>
              editor.chain().focus().toggleBlockquote().run()
            )}
          >
            <Quote className="h-4 w-4" />
          </Button>
          <Button
            type="button"
            size="icon"
            variant="ghost"
            className={TOOLBAR_BUTTON_CLASSES}
            data-active={editor.isActive("codeBlock")}
            onMouseDown={(event) => event.preventDefault()}
            onClick={createToggleHandler(() =>
              editor.chain().focus().toggleCodeBlock().run()
            )}
          >
            <Braces className="h-4 w-4" />
          </Button>

          <div className="mx-2 h-6 w-px bg-border" />

          <Button
            type="button"
            size="icon"
            variant="ghost"
            className={TOOLBAR_BUTTON_CLASSES}
            disabled={!editor.can().chain().focus().undo().run()}
            onMouseDown={(event) => event.preventDefault()}
            onClick={createToggleHandler(() =>
              editor.chain().focus().undo().run()
            )}
          >
            <Undo className="h-4 w-4" />
          </Button>
          <Button
            type="button"
            size="icon"
            variant="ghost"
            className={TOOLBAR_BUTTON_CLASSES}
            disabled={!editor.can().chain().focus().redo().run()}
            onMouseDown={(event) => event.preventDefault()}
            onClick={createToggleHandler(() =>
              editor.chain().focus().redo().run()
            )}
          >
            <Redo className="h-4 w-4" />
          </Button>
        </div>
      )}

      <div className="simple-editor-content">
        <EditorContent editor={editor} onBlur={handleBlur} />
      </div>
    </div>
  )
}
