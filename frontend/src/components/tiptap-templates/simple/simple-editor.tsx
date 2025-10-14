"use client"

import { Highlight } from "@tiptap/extension-highlight"
import { Image } from "@tiptap/extension-image"
import { TaskItem, TaskList } from "@tiptap/extension-list"
import { Subscript } from "@tiptap/extension-subscript"
import { Superscript } from "@tiptap/extension-superscript"
import { TextAlign } from "@tiptap/extension-text-align"
import { Table } from "@tiptap/extension-table"
import { TableCell } from "@tiptap/extension-table-cell"
import { TableHeader } from "@tiptap/extension-table-header"
import { TableRow } from "@tiptap/extension-table-row"
import { Typography } from "@tiptap/extension-typography"
import { Selection } from "@tiptap/extensions"
import {
  EditorContent,
  EditorContext,
  useEditor,
  type Editor,
} from "@tiptap/react"
// --- Tiptap Core Extensions ---
import { StarterKit } from "@tiptap/starter-kit"
import { marked } from "marked"
import * as React from "react"
import TurndownService from "turndown"
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

// --- Icons ---
import { ArrowLeftIcon } from "@/components/tiptap-icons/arrow-left-icon"
import { HighlighterIcon } from "@/components/tiptap-icons/highlighter-icon"
import { LinkIcon } from "@/components/tiptap-icons/link-icon"
import {
  BookmarkX,
  Delete as DeleteIcon,
  PanelBottomOpen,
  PanelLeftOpen,
  PanelRightOpen,
  PanelTopOpen,
  Table as TableIcon,
} from "lucide-react"
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

const ELEMENT_NODE = 1
const TABLE_ALIGN_MAP = {
  center: ":-:",
  left: ":--",
  right: "--:",
} as const
const TABLE_SECTION_TAGS = new Set(["THEAD", "TBODY", "TFOOT"])

const asElement = (node: TurndownService.Node | null): HTMLElement | null => {
  if (
    node &&
    typeof node === "object" &&
    "nodeType" in node &&
    (node as Node).nodeType === ELEMENT_NODE &&
    "getAttribute" in (node as Element)
  ) {
    return node as HTMLElement
  }
  return null
}

const getNodeIndex = (node: Node): number => {
  const parent = node.parentNode
  if (!parent) {
    return -1
  }
  return Array.from(parent.childNodes).indexOf(node as ChildNode)
}

const renderTableCell = (content: string, node: HTMLElement): string => {
  const index = getNodeIndex(node)
  const prefix = index === 0 ? "| " : " "
  return `${prefix}${content} |`
}

const isFirstTbody = (element: Node | null): element is HTMLTableSectionElement => {
  if (!(element instanceof HTMLTableSectionElement) || element.nodeName !== "TBODY") {
    return false
  }

  const previousSibling = element.previousSibling
  if (!previousSibling) {
    return true
  }

  return (
    previousSibling.nodeName === "THEAD" &&
    /^\s*$/i.test(previousSibling.textContent ?? "")
  )
}

const isHeadingRow = (
  row: Element | null
): row is HTMLTableRowElement => {
  if (!(row instanceof HTMLTableRowElement)) {
    return false
  }

  const parentNode = row.parentNode
  if (!parentNode) {
    return false
  }

  if (parentNode.nodeName === "THEAD") {
    return true
  }

  if (
    parentNode.firstChild === row &&
    (parentNode.nodeName === "TABLE" || isFirstTbody(parentNode))
  ) {
    return Array.from(row.childNodes).every(
      (child) => child instanceof HTMLTableCellElement && child.nodeName === "TH"
    )
  }

  return false
}

const registerMarkdownTableRules = (service: TurndownService) => {
  service.keep((node) => {
    const element = asElement(node)
    if (!element || element.nodeName !== "TABLE") {
      return false
    }

    const table = element as HTMLTableElement
    const firstRow = table.rows?.[0] ?? null
    return !isHeadingRow(firstRow)
  })

  service.addRule("tableSection", {
    filter: (node: TurndownService.Node) => {
      const element = asElement(node)
      return !!element && TABLE_SECTION_TAGS.has(element.nodeName)
    },
    replacement: (content: string) => content,
  })

  service.addRule("tableRow", {
    filter: (node: TurndownService.Node) => {
      const element = asElement(node)
      return !!element && element.nodeName === "TR"
    },
    replacement: (content: string, node: TurndownService.Node) => {
      const element = asElement(node)
      if (!element) {
        return content
      }

      let borderCells = ""

      if (isHeadingRow(element)) {
        borderCells = Array.from(element.childNodes)
          .map((child) => {
            if (!(child instanceof HTMLElement)) {
              return ""
            }

            let border = "---"
            const alignAttr =
              child.getAttribute("align") ??
              child.getAttribute("data-align") ??
              child.style?.textAlign ??
              ""
            const alignKey = alignAttr.toLowerCase()
            if (alignKey && alignKey in TABLE_ALIGN_MAP) {
              border =
                TABLE_ALIGN_MAP[alignKey as keyof typeof TABLE_ALIGN_MAP] ?? border
            }

            return renderTableCell(border, child)
          })
          .join("")
      }

      return `\n${content}${borderCells ? `\n${borderCells}` : ""}`
    },
  })

  service.addRule("tableCell", {
    filter: (node: TurndownService.Node) => {
      const element = asElement(node)
      return !!element && (element.nodeName === "TH" || element.nodeName === "TD")
    },
    replacement: (content: string, node: TurndownService.Node) => {
      const element = asElement(node)
      if (!element) {
        return content
      }

      return renderTableCell(content, element)
    },
  })

  service.addRule("table", {
    filter: (node: TurndownService.Node) => {
      const element = asElement(node)
      if (!element || element.nodeName !== "TABLE") {
        return false
      }

      const table = element as HTMLTableElement
      if (!table.rows || table.rows.length === 0) {
        return false
      }

      return isHeadingRow(table.rows[0] ?? null)
    },
    replacement: (content: string) => {
      const normalized = content.replace(/\n{2,}/g, "\n")
      return `\n\n${normalized}\n\n`
    },
  })
}

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

  const insertTableOptions = { rows: 3, cols: 3, withHeaderRow: true } as const

  const insertButtons: TableButton[] = [
    {
      key: "insert-table",
      tooltip: "Insert table",
      disabled: !editor.can().insertTable(insertTableOptions),
      onClick: () =>
        editor.chain().focus().insertTable(insertTableOptions).run(),
      icon: <TableIcon className="tiptap-button-icon" />,
    },
  ]

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
      ]
    : []

  return { insertButtons, deleteButtons }
}

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
  const { editor } = useTiptapEditor()
  const hasEditableEditor = !!editor && editor.isEditable
  const isTableActive = !!editor && editor.isActive("table")
  const tableButtonGroups = React.useMemo<TableButtonGroups>(
    () => {
      if (!editor || !hasEditableEditor) {
        return { insertButtons: [], deleteButtons: [] }
      }
      return getTableButtonGroups(editor, isTableActive)
    },
    [editor, hasEditableEditor, isTableActive]
  )
  const hasInsertButtons = tableButtonGroups.insertButtons.length > 0
  const hasDeleteButtons = tableButtonGroups.deleteButtons.length > 0
  const shouldShowThemeSeparator =
    darkMode && (isMobile || hasDeleteButtons)

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
  const [isEditorFocused, setIsEditorFocused] = React.useState(false)
  const [isToolbarFocused, setIsToolbarFocused] = React.useState(false)
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

    service.addRule("taskItemLabel", {
      filter: (node: TurndownService.Node) => {
        const element = asElement(node)
        return (
          !!element &&
          element.nodeName === "LABEL" &&
          element.parentElement?.getAttribute("data-type") === "taskItem"
        )
      },
      replacement: () => "",
    })

    service.addRule("taskItem", {
      filter: (node: TurndownService.Node) => {
        const element = asElement(node)
        return (
          !!element &&
          element.nodeName === "LI" &&
          element.getAttribute("data-type") === "taskItem"
        )
      },
      replacement: (content: string, node: TurndownService.Node) => {
        const element = asElement(node)
        if (!element) {
          return content
        }

        const isChecked = element.getAttribute("data-checked") === "true"
        const prefix = `- [${isChecked ? "x" : " "}] `
        const normalized = content.replace(/^\n+/, "").replace(/\n+$/, "")
        const lines = normalized.split("\n")
        const formatted = lines
          .map((line, index) => {
            if (index === 0) return line
            if (!line.trim()) return line
            return " ".repeat(prefix.length) + line
          })
          .join("\n")
        const suffix = element.nextSibling ? "\n" : ""
        return `${prefix}${formatted}${suffix}`
      },
    })

    registerMarkdownTableRules(service)

    return service
  }, [])

  const markedRenderer = React.useMemo(() => {
    const renderer = new marked.Renderer()
    const originalList = renderer.list.bind(renderer)
    const originalListItem = renderer.listitem.bind(renderer)

    let renderingTaskList = false

    renderer.list = function (token) {
      if (token.items.length === 0) {
        return originalList(token)
      }

      const isTaskList = token.items.every((item) => item.task)
      if (!isTaskList) {
        return originalList(token)
      }

      const previous = renderingTaskList
      renderingTaskList = true
      try {
        const body = token.items.map((item) => this.listitem(item)).join("")
        return `<ul data-type="taskList">\n${body}</ul>\n`
      } finally {
        renderingTaskList = previous
      }
    }

    renderer.listitem = function (item) {
      if (!renderingTaskList || !item.task) {
        return originalListItem(item)
      }

      const checkboxMarkup = `<label><input type="checkbox"${
        item.checked ? ' checked="checked"' : ""
      }><span></span></label>`
      const content = this.parser.parse(item.tokens, !!item.loose)

      return `<li data-type="taskItem" data-checked="${
        item.checked ? "true" : "false"
      }">${checkboxMarkup}<div>${content}</div></li>\n`
    }

    return renderer
  }, [])

  const toHtml = React.useCallback(
    (markdown: string): string => {
      const source = markdown ?? ""
      if (!source.trim()) {
        return ""
      }
      return (
        (marked.parse(source, {
          async: false,
          renderer: markedRenderer,
        }) as string) ?? ""
      )
    },
    [markedRenderer]
  )

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
    onBlur: ({ event }) => {
      const nextTarget = event?.relatedTarget
      if (
        nextTarget instanceof Node &&
        toolbarRef.current?.contains(nextTarget)
      ) {
        setIsEditorFocused(true)
      } else {
        setIsEditorFocused(false)
      }
      onBlur?.()
    },
    onFocus: () => {
      setIsEditorFocused(true)
      onFocus?.()
    },
  })

  const canRenderToolbar = showToolbar && editable
  const shouldShowToolbar =
    canRenderToolbar && (isEditorFocused || isToolbarFocused)

  const rect = useCursorVisibility({
    editor,
    overlayHeight: shouldShowToolbar
      ? (toolbarRef.current?.getBoundingClientRect().height ?? 0)
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
            onFocusCapture={() => {
              if (!shouldShowToolbar) return
              setIsToolbarFocused(true)
            }}
            onBlurCapture={(event) => {
              if (!shouldShowToolbar) {
                setIsToolbarFocused(false)
                return
              }
              const nextTarget = event.relatedTarget
              if (
                nextTarget instanceof Node &&
                toolbarRef.current?.contains(nextTarget)
              ) {
                return
              }
              setIsToolbarFocused(false)
            }}
            style={{
              marginBottom: 0,
              paddingBottom: 0,
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
