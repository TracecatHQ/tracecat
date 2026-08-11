"use client"

import type { EditorView } from "@tiptap/pm/view"
import type { Editor } from "@tiptap/react"
import * as React from "react"
import { createPortal } from "react-dom"
// --- Icons ---
import { CornerDownLeftIcon } from "@/components/tiptap-icons/corner-down-left-icon"
import { ExternalLinkIcon } from "@/components/tiptap-icons/external-link-icon"
import { LinkIcon } from "@/components/tiptap-icons/link-icon"
import { TrashIcon } from "@/components/tiptap-icons/trash-icon"
// --- Tiptap UI ---
import type { UseLinkPopoverConfig } from "@/components/tiptap-ui/link-popover"
import { useLinkPopover } from "@/components/tiptap-ui/link-popover"
// --- UI Primitives ---
import type { ButtonProps } from "@/components/tiptap-ui-primitive/button"
import { Button, ButtonGroup } from "@/components/tiptap-ui-primitive/button"
import {
  Card,
  CardBody,
  CardItemGroup,
} from "@/components/tiptap-ui-primitive/card"
import { Input, InputGroup } from "@/components/tiptap-ui-primitive/input"
import {
  Popover,
  PopoverAnchor,
  PopoverContent,
  PopoverTrigger,
} from "@/components/tiptap-ui-primitive/popover"
import { Separator } from "@/components/tiptap-ui-primitive/separator"
// --- Hooks ---
import { useIsMobile } from "@/hooks/use-mobile"
import { useTiptapEditor } from "@/hooks/use-tiptap-editor"
import "@/components/tiptap-ui/link-popover/link-popover.scss"

/** Viewport-space rect the link popover is anchored to. */
interface AnchorRect {
  left: number
  top: number
  width: number
  height: number
}

/** True when two anchor rects describe the same position and size. */
function isSameAnchorRect(a: AnchorRect | null, b: AnchorRect): boolean {
  return (
    a !== null &&
    a.left === b.left &&
    a.top === b.top &&
    a.width === b.width &&
    a.height === b.height
  )
}

/**
 * Find the anchor element that renders the document position, if any.
 */
function findLinkElementAtPos(
  view: EditorView,
  pos: number
): HTMLElement | null {
  const { node } = view.domAtPos(pos)
  if (node.nodeType === Node.ELEMENT_NODE) {
    return (node as HTMLElement).closest("a")
  }
  return node.parentElement?.closest("a") ?? null
}

/**
 * Resolve the viewport rect the link popover should be anchored to. Prefers the
 * rendered anchor element and falls back to the selection coordinates.
 */
function resolveLinkAnchorRect(editor: Editor): DOMRect {
  const { view } = editor
  const { from, to } = editor.state.selection
  try {
    const linkElement = findLinkElementAtPos(view, from)
    if (linkElement) {
      return linkElement.getBoundingClientRect()
    }
    const start = view.coordsAtPos(from)
    const end = view.coordsAtPos(to)
    const left = Math.min(start.left, end.left)
    const top = Math.min(start.top, end.top)
    return DOMRect.fromRect({
      x: left,
      y: top,
      width: Math.max(start.right, end.right) - left,
      height: Math.max(start.bottom, end.bottom) - top,
    })
  } catch {
    return view.dom.getBoundingClientRect()
  }
}

export interface LinkMainProps {
  /**
   * The URL to set for the link.
   */
  url: string
  /**
   * Function to update the URL state.
   */
  setUrl: React.Dispatch<React.SetStateAction<string | null>>
  /**
   * Function to set the link in the editor.
   */
  setLink: () => void
  /**
   * Function to remove the link from the editor.
   */
  removeLink: () => void
  /**
   * Function to open the link.
   */
  openLink: () => void
  /**
   * Whether the link is currently active in the editor.
   */
  isActive: boolean
}

export interface LinkPopoverProps
  extends Omit<ButtonProps, "type">,
    UseLinkPopoverConfig {
  /**
   * Callback for when the popover opens or closes.
   */
  onOpenChange?: (isOpen: boolean) => void
  /**
   * Whether to automatically open the popover when a link is active.
   * @default true
   */
  autoOpenOnLinkActive?: boolean
}

/**
 * Link button component for triggering the link popover
 */
export const LinkButton = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, children, ...props }, ref) => {
    return (
      <Button
        type="button"
        className={className}
        data-style="ghost"
        role="button"
        aria-label="Link"
        tooltip="Link"
        ref={ref}
        {...props}
      >
        {children || <LinkIcon className="tiptap-button-icon" />}
      </Button>
    )
  }
)

LinkButton.displayName = "LinkButton"

/**
 * Main content component for the link popover
 */
const LinkMain: React.FC<LinkMainProps> = ({
  url,
  setUrl,
  setLink,
  removeLink,
  openLink,
  isActive,
}) => {
  const isMobile = useIsMobile()

  const handleKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "Enter") {
      event.preventDefault()
      setLink()
    }
  }

  return (
    <Card
      style={{
        ...(isMobile ? { boxShadow: "none", border: 0 } : {}),
      }}
    >
      <CardBody
        style={{
          ...(isMobile ? { padding: 0 } : {}),
        }}
      >
        <CardItemGroup orientation="horizontal">
          <InputGroup>
            <Input
              type="url"
              placeholder="Paste a link..."
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={handleKeyDown}
              onFocus={(e) => {
                // Select for quick replacement, but keep the start of a long URL
                // in view rather than the tail the caret would scroll to.
                e.currentTarget.select()
                e.currentTarget.scrollLeft = 0
              }}
              autoFocus
              autoComplete="off"
              autoCorrect="off"
              autoCapitalize="off"
            />
          </InputGroup>

          <ButtonGroup orientation="horizontal">
            <Button
              type="button"
              onClick={setLink}
              title="Apply link"
              disabled={!url && !isActive}
              data-style="ghost"
            >
              <CornerDownLeftIcon className="tiptap-button-icon" />
            </Button>
          </ButtonGroup>

          <Separator />

          <ButtonGroup orientation="horizontal">
            <Button
              type="button"
              onClick={openLink}
              title="Open in new window"
              disabled={!url && !isActive}
              data-style="ghost"
            >
              <ExternalLinkIcon className="tiptap-button-icon" />
            </Button>

            <Button
              type="button"
              onClick={removeLink}
              title="Remove link"
              disabled={!url && !isActive}
              data-style="ghost"
            >
              <TrashIcon className="tiptap-button-icon" />
            </Button>
          </ButtonGroup>
        </CardItemGroup>
      </CardBody>
    </Card>
  )
}

/**
 * Link content component for standalone use
 */
export const LinkContent: React.FC<{
  editor?: Editor | null
}> = ({ editor }) => {
  const linkPopover = useLinkPopover({
    editor,
  })

  return <LinkMain {...linkPopover} />
}

/**
 * Link popover component for Tiptap editors.
 *
 * For custom popover implementations, use the `useLinkPopover` hook instead.
 */
export const LinkPopover = React.forwardRef<
  HTMLButtonElement,
  LinkPopoverProps
>(
  (
    {
      editor: providedEditor,
      hideWhenUnavailable = false,
      onSetLink,
      onOpenChange,
      autoOpenOnLinkActive = true,
      onClick,
      children,
      ...buttonProps
    },
    ref
  ) => {
    const { editor } = useTiptapEditor(providedEditor)
    const [isOpen, setIsOpen] = React.useState(false)

    // Viewport rect of the link the popover points at. Tracked in state (rather
    // than a virtual anchor ref) so Radix always measures a real, mounted node.
    const [anchorRect, setAnchorRect] = React.useState<AnchorRect | null>(null)

    const {
      isVisible,
      canSet,
      isActive,
      url,
      setUrl,
      setLink,
      removeLink,
      openLink,
      label,
      Icon,
    } = useLinkPopover({
      editor,
      hideWhenUnavailable,
      onSetLink,
    })

    const handleOnOpenChange = React.useCallback(
      (nextIsOpen: boolean) => {
        setIsOpen(nextIsOpen)
        onOpenChange?.(nextIsOpen)
      },
      [onOpenChange]
    )

    const handleSetLink = React.useCallback(() => {
      setLink()
      setIsOpen(false)
    }, [setLink])

    const handleClick = React.useCallback(
      (event: React.MouseEvent<HTMLButtonElement>) => {
        onClick?.(event)
        if (event.defaultPrevented) return
        setIsOpen(!isOpen)
      },
      [onClick, isOpen]
    )

    React.useEffect(() => {
      if (autoOpenOnLinkActive && isActive) {
        setIsOpen(true)
      }
    }, [autoOpenOnLinkActive, isActive])

    // Keep the anchor over the link while the popover is open, including as the
    // page scrolls or resizes underneath it.
    React.useEffect(() => {
      if (!isOpen || !editor?.view) {
        setAnchorRect(null)
        return
      }

      const syncAnchorRect = () => {
        const rect = resolveLinkAnchorRect(editor)
        const next: AnchorRect = {
          left: rect.left,
          top: rect.top,
          width: rect.width,
          height: rect.height,
        }
        setAnchorRect((prev) => (isSameAnchorRect(prev, next) ? prev : next))
      }

      syncAnchorRect()
      window.addEventListener("scroll", syncAnchorRect, true)
      window.addEventListener("resize", syncAnchorRect)
      return () => {
        window.removeEventListener("scroll", syncAnchorRect, true)
        window.removeEventListener("resize", syncAnchorRect)
      }
    }, [isOpen, editor])

    if (!isVisible) {
      return null
    }

    return (
      <Popover open={isOpen} onOpenChange={handleOnOpenChange}>
        {anchorRect
          ? createPortal(
              <PopoverAnchor
                aria-hidden
                style={{
                  position: "fixed",
                  left: anchorRect.left,
                  top: anchorRect.top,
                  width: anchorRect.width,
                  height: anchorRect.height,
                  pointerEvents: "none",
                }}
              />,
              document.body
            )
          : null}

        <PopoverTrigger asChild>
          <LinkButton
            disabled={!canSet}
            data-active-state={isActive ? "on" : "off"}
            data-disabled={!canSet}
            aria-label={label}
            aria-pressed={isActive}
            onClick={handleClick}
            {...buttonProps}
            ref={ref}
          >
            {children ?? <Icon className="tiptap-button-icon" />}
          </LinkButton>
        </PopoverTrigger>

        <PopoverContent
          className="tiptap-link-popover"
          align="start"
          collisionPadding={8}
        >
          <LinkMain
            url={url}
            setUrl={setUrl}
            setLink={handleSetLink}
            removeLink={removeLink}
            openLink={openLink}
            isActive={isActive}
          />
        </PopoverContent>
      </Popover>
    )
  }
)

LinkPopover.displayName = "LinkPopover"

export default LinkPopover
