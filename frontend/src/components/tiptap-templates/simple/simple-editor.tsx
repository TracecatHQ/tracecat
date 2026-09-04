"use client"

import { Highlight } from "@tiptap/extension-highlight"
import { Image } from "@tiptap/extension-image"
import { TaskItem, TaskList } from "@tiptap/extension-list"
import { Subscript } from "@tiptap/extension-subscript"
import { Superscript } from "@tiptap/extension-superscript"
import { TableCell } from "@tiptap/extension-table-cell"
import { TableHeader } from "@tiptap/extension-table-header"
import { TableRow } from "@tiptap/extension-table-row"
import { TextAlign } from "@tiptap/extension-text-align"
import { Typography } from "@tiptap/extension-typography"
import { Selection } from "@tiptap/extensions"
import { Markdown } from "@tiptap/markdown"
import type { EditorView } from "@tiptap/pm/view"
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
import { AttachmentImage } from "@/components/tiptap-node/image-node/attachment-image-node"
// --- Tiptap Node ---
import { ImageUploadNode } from "@/components/tiptap-node/image-upload-node/image-upload-node-extension"
import { MermaidCodeBlock } from "@/components/tiptap-node/mermaid-code-block-node/mermaid-code-block-node"
import {
  canMoveTableColumnLeft,
  canMoveTableColumnRight,
  TracecatTable,
} from "@/components/tiptap-node/table-node/table-node-extension"
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

// Panel icons below are chosen for the direction their arrow points, not for
// the panel edge in their name. In lucide, `PanelLeftOpen` draws a
// right-pointing arrow, `PanelRightOpen` a left-pointing one, `PanelTopOpen`
// points down and `PanelBottomOpen` points up. Users read the arrow, so the
// name-to-command pairing looks inverted on purpose. Do not "fix" it.
import {
  ArrowLeftToLine,
  ArrowRightToLine,
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
import {
  createPastedImageFile,
  extractImageFiles,
} from "@/lib/cases/use-case-image-upload"
import {
  AGENT_MENTION_URI_SCHEME,
  WORKFLOW_MENTION_URI_SCHEME,
} from "@/lib/tiptap-comment-mentions"
import {
  handleImageUpload,
  MAX_FILE_SIZE,
  sanitizeUrl,
} from "@/lib/tiptap-utils"
import { cn } from "@/lib/utils"

/** Upload images then insert image nodes at the drop position or selection. */
async function uploadAndInsertImages(
  view: EditorView,
  files: File[],
  upload: (file: File) => Promise<string>,
  startPos?: number
): Promise<void> {
  let insertPos = startPos
  for (const file of files) {
    try {
      const src = await upload(file)
      const imageType = view.state.schema.nodes.image
      if (!imageType) {
        continue
      }
      const node = imageType.create({ src, alt: file.name })
      const pos = insertPos ?? view.state.selection.to
      view.dispatch(view.state.tr.insert(pos, node))
      insertPos = pos + node.nodeSize
    } catch {
      // Upload failures are surfaced by the upload function's own toast.
    }
  }
}

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
  moveButtons: TableButton[]
  deleteButtons: TableButton[]
}

const getTableButtonGroups = (
  editor: Editor,
  isTableActive: boolean
): TableButtonGroups => {
  if (!editor.isEditable) {
    return { insertButtons: [], moveButtons: [], deleteButtons: [] }
  }

  const insertButtons: TableButton[] = []

  if (isTableActive) {
    insertButtons.push(
      {
        key: "add-column-before",
        tooltip: "Insert column to the left",
        disabled: !editor.can().addColumnBefore(),
        onClick: () => editor.chain().focus().addColumnBefore().run(),
        icon: <PanelRightOpen className="tiptap-button-icon" />,
      },
      {
        key: "add-column-after",
        tooltip: "Insert column to the right",
        disabled: !editor.can().addColumnAfter(),
        onClick: () => editor.chain().focus().addColumnAfter().run(),
        icon: <PanelLeftOpen className="tiptap-button-icon" />,
      },
      {
        key: "add-row-before",
        tooltip: "Insert row above",
        disabled: !editor.can().addRowBefore(),
        onClick: () => editor.chain().focus().addRowBefore().run(),
        icon: <PanelBottomOpen className="tiptap-button-icon" />,
      },
      {
        key: "add-row-after",
        tooltip: "Insert row below",
        disabled: !editor.can().addRowAfter(),
        onClick: () => editor.chain().focus().addRowAfter().run(),
        icon: <PanelTopOpen className="tiptap-button-icon" />,
      }
    )
  }

  // The other buttons ask `editor.can()`, which runs their command for real
  // against a throwaway state. That is cheap for every command here except the
  // two moves: `moveTableColumn` transposes and rebuilds the whole table node,
  // and this runs on every transaction while the cursor is in a table, so the
  // two moves ask the shared boundary rule directly instead.
  const moveButtons: TableButton[] = isTableActive
    ? [
        {
          key: "move-column-left",
          tooltip: "Move column left",
          disabled: !canMoveTableColumnLeft(editor.state),
          onClick: () => editor.chain().focus().moveTableColumnLeft().run(),
          icon: <ArrowLeftToLine className="tiptap-button-icon" />,
        },
        {
          key: "move-column-right",
          tooltip: "Move column right",
          disabled: !canMoveTableColumnRight(editor.state),
          onClick: () => editor.chain().focus().moveTableColumnRight().run(),
          icon: <ArrowRightToLine className="tiptap-button-icon" />,
        },
      ]
    : []

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

  return { insertButtons, moveButtons, deleteButtons }
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
  // Deliberately computed during render rather than memoized. Every input a
  // memo could key on is stable while the cursor stays inside one table —
  // `editor` for its whole lifetime, `isTableActive` until the cursor leaves —
  // so the disabled flags would freeze when the cursor entered the table and
  // stop following the caret between columns. `useTiptapEditor` subscribes to
  // `editorState`, which changes on every transaction and re-renders us.
  const tableButtonGroups: TableButtonGroups =
    editor && hasEditableEditor
      ? getTableButtonGroups(editor, isTableActive)
      : { insertButtons: [], moveButtons: [], deleteButtons: [] }
  const shouldShowThemeSeparator = darkMode && (isMobile || isTableActive)

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

      {/* Inside a table this slot becomes the table controls. Headings, lists,
          block quotes, code blocks and nested tables are all noise in a cell,
          and the row/column controls are what the user actually reached for. */}
      {isTableActive ? (
        // Insert, move and delete are separated so nine icons read as three
        // intents rather than one undifferentiated row.
        <>
          <ToolbarGroup className="simple-editor-table-controls">
            {renderButtonGroup(tableButtonGroups.insertButtons)}
          </ToolbarGroup>
          <ToolbarSeparator />
          <ToolbarGroup className="simple-editor-table-controls">
            {renderButtonGroup(tableButtonGroups.moveButtons)}
          </ToolbarGroup>
          <ToolbarSeparator />
          <ToolbarGroup className="simple-editor-table-controls">
            {renderButtonGroup(tableButtonGroups.deleteButtons)}
          </ToolbarGroup>
        </>
      ) : (
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
      )}

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
   * Keep toolbar layout space reserved while hidden.
   * @default false
   */
  preserveToolbarSpace?: boolean
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
   * Render Mermaid code blocks as diagrams when this editable editor is blurred.
   * Read-only editors always render Mermaid diagrams.
   * @default false
   */
  renderMermaidWhenBlurred?: boolean
  /**
   * Auto focus behaviour.
   * @default false
   */
  autoFocus?: boolean
  /**
   * Optional inline styles for the wrapper.
   */
  style?: React.CSSProperties
  /**
   * Enable inline image support: registers the image node so `![](...)`
   * markdown round-trips and, when combined with `onImageUpload`, enables
   * paste/drop uploads.
   * @default false
   */
  enableImages?: boolean
  /**
   * Workspace id used to resolve `attachment://` image srcs at render time.
   * Required for images to display.
   */
  imageWorkspaceId?: string | null
  /**
   * Upload a pasted/dropped image and return its stable markdown src (e.g.
   * `attachment://<caseId>/<attachmentId>`). Required to enable paste/drop.
   */
  onImageUpload?: (file: File) => Promise<string>
  /** Called when the TipTap editor instance becomes available or is removed. */
  onEditorReady?: (editor: Editor | null) => void
}

export function SimpleEditor({
  value,
  onChange,
  editable = true,
  showToolbar = true,
  preserveToolbarSpace = false,
  className,
  placeholder,
  onSave,
  onBlur,
  onShortcutFallback,
  onFocus,
  toolbarStatus,
  renderMermaidWhenBlurred = false,
  autoFocus = false,
  style,
  enableImages = false,
  imageWorkspaceId = null,
  onImageUpload,
  onEditorReady,
}: SimpleEditorProps) {
  const isMobile = useIsMobile()
  const { height } = useWindowSize()
  const [mobileView, setMobileView] = React.useState<
    "main" | "highlighter" | "link"
  >("main")
  const toolbarRef = React.useRef<HTMLDivElement>(null)
  const markdownRef = React.useRef<string>(value ?? "")
  const previousEditableRef = React.useRef(editable)
  const imageUploadRef = React.useRef(onImageUpload)

  React.useEffect(() => {
    imageUploadRef.current = onImageUpload
  }, [onImageUpload])

  const extensions = React.useMemo(
    () => [
      StarterKit.configure({
        horizontalRule: false,
        codeBlock: false,
        link: {
          openOnClick: false,
          enableClickSelection: true,
          isAllowedUri: (url, { defaultValidate }) =>
            url.startsWith(AGENT_MENTION_URI_SCHEME) ||
            url.startsWith(WORKFLOW_MENTION_URI_SCHEME) ||
            defaultValidate(url),
        },
      }),
      HorizontalRule,
      MermaidCodeBlock.configure({
        renderWhenBlurred: renderMermaidWhenBlurred,
      }),
      TracecatTable.configure({
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
      ...(enableImages
        ? [AttachmentImage.configure({ workspaceId: imageWorkspaceId })]
        : []),
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
    [renderMermaidWhenBlurred, enableImages, imageWorkspaceId]
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
      handleClick: (_view, _pos, event) => {
        if (!event.metaKey && !event.ctrlKey) return false
        const href = (event.target as HTMLElement | null)
          ?.closest("a")
          ?.getAttribute("href")
        if (!href) return false
        const safeUrl = sanitizeUrl(href, window.location.href)
        if (safeUrl === "#") return false
        event.preventDefault()
        window.open(safeUrl, "_blank", "noopener,noreferrer")
        return true
      },
      handlePaste: (view, event) => {
        const upload = imageUploadRef.current
        if (!upload) {
          return false
        }
        const files = extractImageFiles(event.clipboardData)
        if (files.length === 0) {
          return false
        }
        event.preventDefault()
        void uploadAndInsertImages(
          view,
          files.map(createPastedImageFile),
          upload
        )
        return true
      },
      handleDrop: (view, event) => {
        const upload = imageUploadRef.current
        if (!upload) {
          return false
        }
        const files = extractImageFiles(event.dataTransfer)
        if (files.length === 0) {
          return false
        }
        event.preventDefault()
        const coords = view.posAtCoords({
          left: event.clientX,
          top: event.clientY,
        })
        void uploadAndInsertImages(view, files, upload, coords?.pos)
        return true
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

  const shouldShowToolbar = showToolbar && editable
  const canRenderToolbar = editable && (showToolbar || preserveToolbarSpace)

  React.useEffect(() => {
    onEditorReady?.(editor)
    return () => onEditorReady?.(null)
  }, [editor, onEditorReady])

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
    // Leave the toolbar surface to CSS: `.simple-editor-toolbar` already
    // defaults it to transparent, and an inline value would outrank any
    // consumer override.
    const next: React.CSSProperties & Record<string, string | number> = {
      paddingBottom: 0,
    }

    if (isMobile) {
      next.marginBottom = 0
      next.bottom = `calc(100% - ${height - rect.y}px)`
    }

    return next
  }, [height, isMobile, rect.y])

  return (
    <div
      className={cn(
        "simple-editor-wrapper",
        !editable && "simple-editor-wrapper--readonly",
        className
      )}
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
          className={cn(
            "simple-editor-content",
            !editable && "simple-editor-content--readonly"
          )}
        />
      </EditorContext.Provider>
    </div>
  )
}
