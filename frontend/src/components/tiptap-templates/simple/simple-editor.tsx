"use client"

import { Highlight } from "@tiptap/extension-highlight"
import { Image } from "@tiptap/extension-image"
import { TaskItem, TaskList } from "@tiptap/extension-list"
import { Subscript } from "@tiptap/extension-subscript"
import { Superscript } from "@tiptap/extension-superscript"
import { Table } from "@tiptap/extension-table"
import { TableCell } from "@tiptap/extension-table-cell"
import { TableHeader } from "@tiptap/extension-table-header"
import { TableRow } from "@tiptap/extension-table-row"
import { TextAlign } from "@tiptap/extension-text-align"
import { Typography } from "@tiptap/extension-typography"
import { Selection } from "@tiptap/extensions"
import { Markdown } from "@tiptap/markdown"
import {
  type Editor,
  EditorContent,
  EditorContext,
  useEditor,
} from "@tiptap/react"
// --- Tiptap Core Extensions ---
import { StarterKit } from "@tiptap/starter-kit"
import * as React from "react"
import { HorizontalRule } from "@/components/tiptap-node/horizontal-rule-node/horizontal-rule-node-extension"
// --- Tiptap Node ---
import { ImageUploadNode } from "@/components/tiptap-node/image-upload-node/image-upload-node-extension"
// --- UI Primitives ---
import { Button, ButtonGroup } from "@/components/tiptap-ui-primitive/button"
import { Spacer } from "@/components/tiptap-ui-primitive/spacer"
import {
  Toolbar,
  ToolbarGroup,
  ToolbarSeparator,
} from "@/components/tiptap-ui-primitive/toolbar"
import "@/components/tiptap-node/blockquote-node/blockquote-node.scss"
import "@/components/tiptap-node/code-block-node/code-block-node.scss"
import "@/components/tiptap-node/horizontal-rule-node/horizontal-rule-node.scss"
import "@/components/tiptap-node/list-node/list-node.scss"
import "@/components/tiptap-node/image-node/image-node.scss"
import "@/components/tiptap-node/heading-node/heading-node.scss"
import "@/components/tiptap-node/paragraph-node/paragraph-node.scss"

import {
  BookmarkX,
  Delete as DeleteIcon,
  PanelBottomOpen,
  PanelLeftOpen,
  PanelRightOpen,
  PanelTopOpen,
  Table as TableIcon,
  Trash2,
} from "lucide-react"
// --- Icons ---
import { ArrowLeftIcon } from "@/components/tiptap-icons/arrow-left-icon"
import { HighlighterIcon } from "@/components/tiptap-icons/highlighter-icon"
import { LinkIcon } from "@/components/tiptap-icons/link-icon"
// --- Components ---
import { ThemeToggle } from "@/components/tiptap-templates/simple/theme-toggle"
import { BlockquoteButton } from "@/components/tiptap-ui/blockquote-button"
import { CodeBlockButton } from "@/components/tiptap-ui/code-block-button"
import {
  ColorHighlightPopover,
  ColorHighlightPopoverButton,
  ColorHighlightPopoverContent,
} from "@/components/tiptap-ui/color-highlight-popover"
// --- Tiptap UI ---
import { HeadingDropdownMenu } from "@/components/tiptap-ui/heading-dropdown-menu"
import { ImageUploadButton } from "@/components/tiptap-ui/image-upload-button"
import {
  LinkButton,
  LinkContent,
  LinkPopover,
} from "@/components/tiptap-ui/link-popover"
import { ListDropdownMenu } from "@/components/tiptap-ui/list-dropdown-menu"
import { MarkButton } from "@/components/tiptap-ui/mark-button"
import { TextAlignButton } from "@/components/tiptap-ui/text-align-button"
import { UndoRedoButton } from "@/components/tiptap-ui/undo-redo-button"
import { useCursorVisibility } from "@/hooks/use-cursor-visibility"
// --- Hooks ---
import { useIsMobile } from "@/hooks/use-mobile"
import { useTiptapEditor } from "@/hooks/use-tiptap-editor"
import { useWindowSize } from "@/hooks/use-window-size"

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

type TableButton = {
  key: string
  tooltip: string
  disabled: boolean
  onClick: () => void
  icon: React.ReactNode
}

interface TableButtonGroups {
  insertButtons: TableButton[]
  deleteButtons: TableButton[]
}

const getTableButtonGroups = (
  editor: Editor,
  isTableActive: boolean
): TableButtonGroups => {
  if (!editor.isEditable) {
    return { insertButtons: [], deleteButtons: [] }
  }

  const insertButtons: TableButton[] = []

  if (isTableActive) {
    insertButtons.push(
      {
        key: "add-column-before",
        tooltip: "Insert column to the left",
        disabled: !editor.can().addColumnBefore(),
        onClick: () => editor.chain().focus().addColumnBefore().run(),
        icon: <PanelLeftOpen className="tiptap-button-icon" />,
      },
      {
        key: "add-column-after",
        tooltip: "Insert column to the right",
        disabled: !editor.can().addColumnAfter(),
        onClick: () => editor.chain().focus().addColumnAfter().run(),
        icon: <PanelRightOpen className="tiptap-button-icon" />,
      },
      {
        key: "add-row-before",
        tooltip: "Insert row above",
        disabled: !editor.can().addRowBefore(),
        onClick: () => editor.chain().focus().addRowBefore().run(),
        icon: <PanelTopOpen className="tiptap-button-icon" />,
      },
      {
        key: "add-row-after",
        tooltip: "Insert row below",
        disabled: !editor.can().addRowAfter(),
        onClick: () => editor.chain().focus().addRowAfter().run(),
        icon: <PanelBottomOpen className="tiptap-button-icon" />,
      }
    )
  }

  const deleteButtons: TableButton[] = isTableActive
    ? [
        {
          key: "delete-column",
          tooltip: "Delete column",
          disabled: !editor.can().deleteColumn(),
          onClick: () => editor.chain().focus().deleteColumn().run(),
          icon: <BookmarkX className="tiptap-button-icon" />,
        },
        {
          key: "delete-row",
          tooltip: "Delete row",
          disabled: !editor.can().deleteRow(),
          onClick: () => editor.chain().focus().deleteRow().run(),
          icon: <DeleteIcon className="tiptap-button-icon" />,
        },
        {
          key: "delete-table",
          tooltip: "Delete table",
          disabled: !editor.can().deleteTable(),
          onClick: () => editor.chain().focus().deleteTable().run(),
          icon: <Trash2 className="tiptap-button-icon" />,
        },
      ]
    : []

  return { insertButtons, deleteButtons }
}

const MainToolbarContent = ({
  onHighlighterClick,
  onLinkClick,
  isMobile,
  features,
  statusIndicator,
}: {
  onHighlighterClick?: () => void
  onLinkClick: () => void
  isMobile: boolean
  features: typeof SIMPLE_EDITOR_FEATURE_FLAGS
  statusIndicator?: React.ReactNode
}) => {
  const { highlight, superSub, textAlign, images, darkMode } = features
  const { editor } = useTiptapEditor()
  const hasEditableEditor = !!editor && editor.isEditable
  const isTableActive = !!editor && editor.isActive("table")
  const handleInsertTable = React.useCallback(() => {
    if (!editor || !editor.isEditable) {
      return
    }

    editor
      .chain()
      .focus()
      .insertTable({ rows: 3, cols: 2, withHeaderRow: true })
      .run()
  }, [editor])
  const canInsertTable = React.useMemo(() => {
    if (!editor || !editor.isEditable) {
      return false
    }

    const can = editor.can()
    if (typeof can.insertTable !== "function") {
      return editor.isEditable
    }

    return can.insertTable({ rows: 3, cols: 2, withHeaderRow: true })
  }, [editor])
  const tableButtonGroups = React.useMemo<TableButtonGroups>(() => {
    if (!editor || !hasEditableEditor) {
      return { insertButtons: [], deleteButtons: [] }
    }
    return getTableButtonGroups(editor, isTableActive)
  }, [editor, hasEditableEditor, isTableActive])
  const hasInsertButtons = tableButtonGroups.insertButtons.length > 0
  const hasDeleteButtons = tableButtonGroups.deleteButtons.length > 0
  const shouldShowThemeSeparator = darkMode && (isMobile || hasDeleteButtons)

  const renderButtonGroup = (buttons: TableButton[]) => (
    <ButtonGroup orientation="horizontal">
      {buttons.map((button) => (
        <Button
          key={button.key}
          type="button"
          data-style="ghost"
          data-disabled={button.disabled}
          disabled={button.disabled}
          tooltip={button.tooltip}
          aria-label={button.tooltip}
          onClick={button.onClick}
        >
          {button.icon}
        </Button>
      ))}
    </ButtonGroup>
  )
  return (
    <>
      <Spacer />

      {statusIndicator && (
        <>
          <ToolbarGroup className="simple-editor-status-indicator">
            {statusIndicator}
          </ToolbarGroup>
          <ToolbarSeparator />
        </>
      )}

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
        <Button
          type="button"
          data-style="ghost"
          data-disabled={!canInsertTable}
          disabled={!canInsertTable}
          tooltip="Insert table"
          aria-label="Insert table"
          onClick={handleInsertTable}
        >
          <TableIcon className="tiptap-button-icon" />
        </Button>
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

      {hasInsertButtons && (
        <>
          <ToolbarSeparator />

          <ToolbarGroup className="simple-editor-table-controls">
            {renderButtonGroup(tableButtonGroups.insertButtons)}
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

      {hasDeleteButtons && (
        <>
          <ToolbarSeparator />

          <ToolbarGroup className="simple-editor-table-controls">
            {renderButtonGroup(tableButtonGroups.deleteButtons)}
          </ToolbarGroup>
        </>
      )}

      {shouldShowThemeSeparator && <ToolbarSeparator />}

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
   * Called when the save shortcut is pressed but no onSave is provided.
   */
  onShortcutFallback?: () => void
  /**
   * Called when the editor gains focus.
   */
  onFocus?: () => void
  /**
   * Optional status indicator rendered in the toolbar.
   */
  toolbarStatus?: React.ReactNode
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
  onShortcutFallback,
  onFocus,
  toolbarStatus,
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
  const previousEditableRef = React.useRef(editable)

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
      Table.configure({
        resizable: false,
      }),
      TableRow,
      TableHeader,
      TableCell,
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
      ...(SIMPLE_EDITOR_FEATURE_FLAGS.superSub ? [Superscript, Subscript] : []),
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
      Markdown.configure({
        markedOptions: {
          gfm: true,
        },
      }),
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
    content: markdownRef.current,
    contentType: "markdown",
    onUpdate: ({ editor }) => {
      if (!onChange || !editor.isEditable) {
        return
      }

      const markdown = editor.getMarkdown()
      if (markdown === markdownRef.current) {
        return
      }

      markdownRef.current = markdown
      onChange(markdown)
    },
    onBlur: () => {
      onBlur?.()
    },
    onFocus: () => {
      onFocus?.()
    },
  })

  const canRenderToolbar = showToolbar && editable
  const shouldShowToolbar = canRenderToolbar

  const rect = useCursorVisibility({
    editor,
    overlayHeight: shouldShowToolbar
      ? (toolbarRef.current?.getBoundingClientRect().height ?? 0)
      : 0,
  })

  React.useEffect(() => {
    const nextMarkdown = value ?? ""

    if (!editor) {
      markdownRef.current = nextMarkdown
      return
    }

    if (nextMarkdown === markdownRef.current) {
      return
    }

    markdownRef.current = nextMarkdown

    if (!nextMarkdown.trim()) {
      editor.commands.clearContent(true)
      return
    }

    editor.commands.setContent(nextMarkdown, {
      contentType: "markdown",
      emitUpdate: false,
    })
  }, [editor, value])

  React.useEffect(() => {
    if (!editor) {
      previousEditableRef.current = editable
      return
    }

    const wasEditable = previousEditableRef.current
    previousEditableRef.current = editable

    editor.setEditable(editable)

    if (wasEditable && !editable) {
      const markdown = markdownRef.current ?? ""

      if (!markdown.trim()) {
        editor.commands.clearContent(true)
        return
      }

      editor.commands.setContent(markdown, {
        contentType: "markdown",
        emitUpdate: false,
      })
    }
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

      if (key === "s") {
        event.preventDefault()
        event.stopPropagation()

        if (!editor.isEditable) {
          return
        }

        if (onSave) {
          onSave()
        } else {
          onShortcutFallback?.() ?? onBlur?.()
        }
      }
    }

    dom.addEventListener("keydown", handleKeydown)

    return () => {
      dom.removeEventListener("keydown", handleKeydown)
    }
  }, [editor, onBlur, onSave, onShortcutFallback])

  const wrapperStyle = React.useMemo<React.CSSProperties>(
    () => ({
      width: "100%",
      height: "auto",
      ...style,
    }),
    [style]
  )
  const toolbarStyle = React.useMemo<
    React.CSSProperties & Record<string, string | number>
  >(() => {
    const next: React.CSSProperties & Record<string, string | number> = {
      paddingBottom: 0,
      "--tt-toolbar-bg-color":
        "color-mix(in srgb, hsl(var(--muted)) 20%, hsl(var(--background)) 80%)",
    }

    if (isMobile) {
      next.marginBottom = 0
      next.bottom = `calc(100% - ${height - rect.y}px)`
    }

    return next
  }, [height, isMobile, rect.y])

  return (
    <div
      className={cn("simple-editor-wrapper", className)}
      style={wrapperStyle}
    >
      <EditorContext.Provider value={{ editor }}>
        {canRenderToolbar && (
          <Toolbar
            ref={toolbarRef}
            variant={isMobile ? "fixed" : "floating"}
            className="simple-editor-toolbar"
            data-visible={shouldShowToolbar ? "true" : "false"}
            aria-hidden={!shouldShowToolbar}
            style={toolbarStyle}
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
                statusIndicator={toolbarStatus}
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
