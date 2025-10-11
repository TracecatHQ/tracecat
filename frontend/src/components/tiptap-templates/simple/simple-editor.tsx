"use client"

import * as React from "react"
import { marked } from "marked"
import TurndownService from "turndown"
import { EditorContent, EditorContext, useEditor } from "@tiptap/react"

// --- Tiptap Core Extensions ---
import { StarterKit } from "@tiptap/starter-kit"
import { Image } from "@tiptap/extension-image"
import { TaskItem, TaskList } from "@tiptap/extension-list"
import { TextAlign } from "@tiptap/extension-text-align"
import { Typography } from "@tiptap/extension-typography"
import { Highlight } from "@tiptap/extension-highlight"
import { Subscript } from "@tiptap/extension-subscript"
import { Superscript } from "@tiptap/extension-superscript"
import { Selection } from "@tiptap/extensions"

// --- UI Primitives ---
import { Button } from "@/components/tiptap-ui-primitive/button"
import { Spacer } from "@/components/tiptap-ui-primitive/spacer"
import {
  Toolbar,
  ToolbarGroup,
  ToolbarSeparator,
} from "@/components/tiptap-ui-primitive/toolbar"

// --- Tiptap Node ---
import { ImageUploadNode } from "@/components/tiptap-node/image-upload-node/image-upload-node-extension"
import { HorizontalRule } from "@/components/tiptap-node/horizontal-rule-node/horizontal-rule-node-extension"
import "@/components/tiptap-node/blockquote-node/blockquote-node.scss"
import "@/components/tiptap-node/code-block-node/code-block-node.scss"
import "@/components/tiptap-node/horizontal-rule-node/horizontal-rule-node.scss"
import "@/components/tiptap-node/list-node/list-node.scss"
import "@/components/tiptap-node/image-node/image-node.scss"
import "@/components/tiptap-node/heading-node/heading-node.scss"
import "@/components/tiptap-node/paragraph-node/paragraph-node.scss"

// --- Tiptap UI ---
import { HeadingDropdownMenu } from "@/components/tiptap-ui/heading-dropdown-menu"
import { ImageUploadButton } from "@/components/tiptap-ui/image-upload-button"
import { ListDropdownMenu } from "@/components/tiptap-ui/list-dropdown-menu"
import { BlockquoteButton } from "@/components/tiptap-ui/blockquote-button"
import { CodeBlockButton } from "@/components/tiptap-ui/code-block-button"
import {
  ColorHighlightPopover,
  ColorHighlightPopoverContent,
  ColorHighlightPopoverButton,
} from "@/components/tiptap-ui/color-highlight-popover"
import {
  LinkPopover,
  LinkContent,
  LinkButton,
} from "@/components/tiptap-ui/link-popover"
import { MarkButton } from "@/components/tiptap-ui/mark-button"
import { TextAlignButton } from "@/components/tiptap-ui/text-align-button"
import { UndoRedoButton } from "@/components/tiptap-ui/undo-redo-button"

// --- Icons ---
import { ArrowLeftIcon } from "@/components/tiptap-icons/arrow-left-icon"
import { HighlighterIcon } from "@/components/tiptap-icons/highlighter-icon"
import { LinkIcon } from "@/components/tiptap-icons/link-icon"

// --- Hooks ---
import { useIsMobile } from "@/hooks/use-mobile"
import { useWindowSize } from "@/hooks/use-window-size"
import { useCursorVisibility } from "@/hooks/use-cursor-visibility"

// --- Components ---
import { ThemeToggle } from "@/components/tiptap-templates/simple/theme-toggle"

// --- Lib ---
import { handleImageUpload, MAX_FILE_SIZE } from "@/lib/tiptap-utils"
import { cn } from "@/lib/utils"

// --- Styles ---
import "@/components/tiptap-templates/simple/simple-editor.scss"

// Feature flags let us retain richer controls while keeping the current Markdown-only surface.
const SIMPLE_EDITOR_FEATURE_FLAGS = {
  highlight: false,
  superSub: false,
  textAlign: false,
  images: false,
  darkMode: false,
} as const

const MainToolbarContent = ({
  onHighlighterClick,
  onLinkClick,
  isMobile,
  features,
}: {
  onHighlighterClick?: () => void
  onLinkClick: () => void
  isMobile: boolean
  features: typeof SIMPLE_EDITOR_FEATURE_FLAGS
}) => {
  const { highlight, superSub, textAlign, images, darkMode } = features
  return (
    <>
      <Spacer />

      <ToolbarGroup>
        <UndoRedoButton action="undo" />
        <UndoRedoButton action="redo" />
      </ToolbarGroup>

      <ToolbarSeparator />

      <ToolbarGroup>
        <HeadingDropdownMenu levels={[1, 2, 3, 4]} portal={isMobile} />
        <ListDropdownMenu
          types={["bulletList", "orderedList", "taskList"]}
          portal={isMobile}
        />
        <BlockquoteButton />
        <CodeBlockButton />
      </ToolbarGroup>

      <ToolbarSeparator />

      <ToolbarGroup>
        <MarkButton type="bold" />
        <MarkButton type="italic" />
        <MarkButton type="strike" />
        <MarkButton type="code" />
        <MarkButton type="underline" />
        {highlight &&
          (!isMobile ? (
            <ColorHighlightPopover />
          ) : (
            <ColorHighlightPopoverButton onClick={onHighlighterClick} />
          ))}
        {!isMobile ? <LinkPopover /> : <LinkButton onClick={onLinkClick} />}
      </ToolbarGroup>

      {superSub && (
        <>
          <ToolbarSeparator />

          <ToolbarGroup>
            <MarkButton type="superscript" />
            <MarkButton type="subscript" />
          </ToolbarGroup>
        </>
      )}

      {textAlign && (
        <>
          <ToolbarSeparator />

          <ToolbarGroup>
            <TextAlignButton align="left" />
            <TextAlignButton align="center" />
            <TextAlignButton align="right" />
            <TextAlignButton align="justify" />
          </ToolbarGroup>
        </>
      )}

      {images && (
        <>
          <ToolbarSeparator />

          <ToolbarGroup>
            <ImageUploadButton text="Add" />
          </ToolbarGroup>
        </>
      )}

      <Spacer />

      {isMobile && darkMode && <ToolbarSeparator />}

      {darkMode && (
        <ToolbarGroup>
          <ThemeToggle />
        </ToolbarGroup>
      )}
    </>
  )
}

const MobileToolbarContent = ({
  type,
  onBack,
  features,
}: {
  type: "highlighter" | "link"
  onBack: () => void
  features: typeof SIMPLE_EDITOR_FEATURE_FLAGS
}) => (
  <>
    <ToolbarGroup>
      <Button data-style="ghost" onClick={onBack}>
        <ArrowLeftIcon className="tiptap-button-icon" />
        {type === "highlighter" && features.highlight ? (
          <HighlighterIcon className="tiptap-button-icon" />
        ) : (
          <LinkIcon className="tiptap-button-icon" />
        )}
      </Button>
    </ToolbarGroup>

    <ToolbarSeparator />

    {type === "highlighter" && features.highlight ? (
      <ColorHighlightPopoverContent />
    ) : (
      <LinkContent />
    )}
  </>
)

export interface SimpleEditorProps {
  /**
   * Markdown content rendered by the editor.
   */
  value?: string
  /**
   * Callback fired with Markdown when the editor content changes.
   */
  onChange?: (value: string) => void
  /**
   * Whether the editor is editable.
   * @default true
   */
  editable?: boolean
  /**
   * Whether to display the toolbar.
   * @default true
   */
  showToolbar?: boolean
  /**
   * Optional wrapper class name for layout overrides.
   */
  className?: string
  /**
   * Placeholder text displayed when the editor is empty.
   */
  placeholder?: string
  /**
   * Called when the editor save shortcut is triggered.
   */
  onSave?: () => void
  /**
   * Called when the editor loses focus.
   */
  onBlur?: () => void
  /**
   * Called when the editor gains focus.
   */
  onFocus?: () => void
  /**
   * Auto focus behaviour.
   * @default false
   */
  autoFocus?: boolean
  /**
   * Optional inline styles for the wrapper.
   */
  style?: React.CSSProperties
}

export function SimpleEditor({
  value,
  onChange,
  editable = true,
  showToolbar = true,
  className,
  placeholder,
  onSave,
  onBlur,
  onFocus,
  autoFocus = false,
  style,
}: SimpleEditorProps) {
  const isMobile = useIsMobile()
  const { height } = useWindowSize()
  const [mobileView, setMobileView] = React.useState<
    "main" | "highlighter" | "link"
  >("main")
  const toolbarRef = React.useRef<HTMLDivElement>(null)
  const markdownRef = React.useRef<string>(value ?? "")

  const turndownService = React.useMemo(() => {
    const service = new TurndownService({
      headingStyle: "atx",
      codeBlockStyle: "fenced",
      bulletListMarker: "-",
    })

    service.addRule("codeBlockLanguage", {
      filter: (node: HTMLElement) =>
        node.nodeName === "PRE" &&
        node.firstChild instanceof HTMLElement &&
        node.firstChild.nodeName === "CODE",
      replacement: (content: string, node: TurndownService.Node) => {
        if (!(node instanceof HTMLElement)) {
          return content
        }

        const codeElement = node.firstElementChild as HTMLElement | null
        const rawLanguage =
          codeElement?.getAttribute("data-language") ??
          codeElement
            ?.getAttribute("class")
            ?.split(" ")
            .find((token) => token.startsWith("language-"))
        const language = rawLanguage?.replace("language-", "") ?? ""
        const fence = "```"
        const contentText = codeElement?.textContent ?? content

        return `\n\n${fence}${language ? ` ${language}` : ""}\n${contentText}\n${fence}\n\n`
      },
    })

    return service
  }, [])

  const toHtml = React.useCallback((markdown: string): string => {
    const source = markdown ?? ""
    if (!source.trim()) {
      return ""
    }
    return (marked.parse(source, { async: false }) as string) ?? ""
  }, [])

  const toMarkdown = React.useCallback(
    (html: string): string => {
      const source = html ?? ""
      if (!source.trim()) {
        return ""
      }
      return turndownService.turndown(source)
    },
    [turndownService]
  )

  const extensions = React.useMemo(
    () => [
      StarterKit.configure({
        horizontalRule: false,
        link: {
          openOnClick: false,
          enableClickSelection: true,
        },
      }),
      HorizontalRule,
      ...(SIMPLE_EDITOR_FEATURE_FLAGS.textAlign
        ? [TextAlign.configure({ types: ["heading", "paragraph"] })]
        : []),
      TaskList,
      TaskItem.configure({ nested: true }),
      ...(SIMPLE_EDITOR_FEATURE_FLAGS.highlight
        ? [Highlight.configure({ multicolor: true })]
        : []),
      ...(SIMPLE_EDITOR_FEATURE_FLAGS.images ? [Image] : []),
      Typography,
      ...(SIMPLE_EDITOR_FEATURE_FLAGS.superSub
        ? [Superscript, Subscript]
        : []),
      Selection,
      ...(SIMPLE_EDITOR_FEATURE_FLAGS.images
        ? [
            ImageUploadNode.configure({
              accept: "image/*",
              maxSize: MAX_FILE_SIZE,
              limit: 3,
              upload: handleImageUpload,
              onError: (error) => console.error("Upload failed:", error),
            }),
          ]
        : []),
    ],
    []
  )

  const editor = useEditor({
    immediatelyRender: false,
    shouldRerenderOnTransaction: false,
    editable,
    autofocus: autoFocus ? "end" : false,
    editorProps: {
      attributes: {
        autocomplete: "off",
        autocorrect: "off",
        autocapitalize: "off",
        "aria-label": "Main content area, start typing to enter text.",
        class: cn("simple-editor", !editable && "simple-editor--readonly"),
        ...(placeholder ? { "data-placeholder": placeholder } : {}),
      },
    },
    extensions,
    content: toHtml(markdownRef.current),
    onUpdate: ({ editor }) => {
      if (!onChange || !editor.isEditable) {
        return
      }

      const markdown = toMarkdown(editor.getHTML())
      if (markdown === markdownRef.current) {
        return
      }

      markdownRef.current = markdown
      onChange(markdown)
    },
    onBlur: () => onBlur?.(),
    onFocus: () => onFocus?.(),
  })

  const shouldShowToolbar = showToolbar && editable

  const rect = useCursorVisibility({
    editor,
    overlayHeight: shouldShowToolbar
      ? toolbarRef.current?.getBoundingClientRect().height ?? 0
      : 0,
  })

  React.useEffect(() => {
    const nextMarkdown = value ?? ""
    if (!editor || nextMarkdown === markdownRef.current) {
      markdownRef.current = nextMarkdown
      return
    }

    const nextHtml = toHtml(nextMarkdown)
    const currentHtml = editor.getHTML()

    if (!nextHtml.trim()) {
      if (!currentHtml.trim()) {
        markdownRef.current = ""
        return
      }
      markdownRef.current = ""
      editor.commands.clearContent(true)
      return
    }

    if (nextHtml.trim() === currentHtml.trim()) {
      markdownRef.current = nextMarkdown
      return
    }

    editor.commands.setContent(nextHtml, { emitUpdate: false })
    markdownRef.current = nextMarkdown
  }, [editor, value, toHtml])

  React.useEffect(() => {
    if (!editor) return
    editor.setEditable(editable)
  }, [editor, editable])

  React.useEffect(() => {
    if (!shouldShowToolbar) {
      setMobileView("main")
      return
    }

    if (!isMobile && mobileView !== "main") {
      setMobileView("main")
    }
  }, [isMobile, mobileView, shouldShowToolbar])

  React.useEffect(() => {
    if (!editor) return

    const dom = editor.view.dom

    const handleKeydown = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey)) {
        return
      }

      const key = event.key.toLowerCase()

      if (key === "enter") {
        event.preventDefault()
        event.stopPropagation()
        return
      }

      if (key === "s") {
        event.preventDefault()
        event.stopPropagation()

        if (!editor.isEditable) {
          return
        }

        if (onSave) {
          onSave()
        } else {
          onBlur?.()
        }
      }
    }

    dom.addEventListener("keydown", handleKeydown)

    return () => {
      dom.removeEventListener("keydown", handleKeydown)
    }
  }, [editor, onBlur, onSave])

  const wrapperStyle = React.useMemo<React.CSSProperties>(
    () => ({
      width: "100%",
      height: "auto",
      ...style,
    }),
    [style]
  )

  return (
    <div className={cn("simple-editor-wrapper", className)} style={wrapperStyle}>
      <EditorContext.Provider value={{ editor }}>
        {shouldShowToolbar && (
          <Toolbar
            ref={toolbarRef}
            variant={isMobile ? "fixed" : "floating"}
            className="simple-editor-toolbar"
            style={{
              ...(isMobile
                ? {
                    bottom: `calc(100% - ${height - rect.y}px)`,
                  }
                : {}),
            }}
          >
            {mobileView === "main" ? (
              <MainToolbarContent
                onHighlighterClick={
                  SIMPLE_EDITOR_FEATURE_FLAGS.highlight
                    ? () => setMobileView("highlighter")
                    : undefined
                }
                onLinkClick={() => setMobileView("link")}
                isMobile={isMobile}
                features={SIMPLE_EDITOR_FEATURE_FLAGS}
              />
            ) : (
              <MobileToolbarContent
                type={
                  mobileView === "highlighter" &&
                  SIMPLE_EDITOR_FEATURE_FLAGS.highlight
                    ? "highlighter"
                    : "link"
                }
                onBack={() => setMobileView("main")}
                features={SIMPLE_EDITOR_FEATURE_FLAGS}
              />
            )}
          </Toolbar>
        )}

        <EditorContent
          editor={editor}
          role="presentation"
          className="simple-editor-content"
        />
      </EditorContext.Provider>
    </div>
  )
}
