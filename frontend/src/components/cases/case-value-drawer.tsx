"use client"

import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react"
import { createPortal } from "react-dom"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { cn } from "@/lib/utils"

/** What the page's one drawer is currently showing, and for whom. */
interface CaseValueDrawerEntry {
  id: string
  title: string
  description?: string
  size?: CaseValueDrawerSize
  /** Tells the owner to close, when the drawer is dismissed or taken over. */
  onClose: () => void
}

interface CaseValueDrawerContextValue {
  activeId: string | null
  /** The drawer's body element, which the owner portals its content into. */
  bodyElement: HTMLElement | null
  claim: (entry: CaseValueDrawerEntry) => void
  release: (id: string) => void
}

/** `wide` suits list content, such as a row's related cases. */
export type CaseValueDrawerSize = "default" | "wide"

const DRAWER_WIDTH_CLASS: Record<CaseValueDrawerSize, string> = {
  default: "w-[min(36rem,100vw)]",
  wide: "w-[min(60rem,100vw)]",
}

const CaseValueDrawerContext =
  createContext<CaseValueDrawerContextValue | null>(null)

function preventDismiss(event: Event) {
  event.preventDefault()
}

interface CaseValueDrawerShellProps {
  open: boolean
  onClose: () => void
  title: string
  description?: string
  size?: CaseValueDrawerSize
  bodyRef?: (element: HTMLDivElement | null) => void
  children?: ReactNode
}

/**
 * The drawer itself: a non-modal sheet on the left edge. Non-modal is the
 * point — Radix then renders no overlay, traps no focus, locks no scroll and
 * hides nothing from assistive tech, so the rest of the page (the chat on the
 * right in particular) stays fully usable while a value is open.
 *
 * It closes only through its close button, the editor's own Save, or another
 * value taking it over. Nothing outside dismisses it, and neither does Escape:
 * the dialogs this replaced blocked Escape so a draft could not be lost to it,
 * and Escape elsewhere on the page stays that surface's own.
 */
function CaseValueDrawerShell({
  open,
  onClose,
  title,
  description,
  size = "default",
  bodyRef,
  children,
}: CaseValueDrawerShellProps) {
  return (
    <Sheet
      modal={false}
      open={open}
      onOpenChange={(nextOpen) => {
        if (!nextOpen) onClose()
      }}
    >
      <SheetContent
        side="left"
        className={cn(
          "flex max-w-none flex-col gap-0 p-0 shadow-none sm:max-w-none",
          DRAWER_WIDTH_CLASS[size]
        )}
        onPointerDownOutside={preventDismiss}
        onInteractOutside={preventDismiss}
        onFocusOutside={preventDismiss}
        onEscapeKeyDown={preventDismiss}
      >
        <SheetHeader className="shrink-0 space-y-1 border-b px-4 py-3 pr-12 text-left">
          <SheetTitle className="truncate text-sm font-medium">
            {title}
          </SheetTitle>
          <SheetDescription className={description ? "text-xs" : "sr-only"}>
            {description ?? title}
          </SheetDescription>
        </SheetHeader>
        <div ref={bodyRef} className="min-h-0 flex-1 overflow-hidden">
          {children}
        </div>
      </SheetContent>
    </Sheet>
  )
}

/**
 * Hosts the case page's single value drawer. Every {@link CaseValueDrawer}
 * below it shares the one drawer: opening a second replaces the first's
 * content in place and tells the first to close, so drawers never stack.
 */
export function CaseValueDrawerProvider({ children }: { children: ReactNode }) {
  const [active, setActive] = useState<CaseValueDrawerEntry | null>(null)
  const activeRef = useRef<CaseValueDrawerEntry | null>(null)
  const [bodyElement, setBodyElement] = useState<HTMLElement | null>(null)
  // Keeps the heading in place while the drawer slides out.
  const lastActiveRef = useRef<CaseValueDrawerEntry | null>(null)
  if (active) {
    lastActiveRef.current = active
  }

  const claim = useCallback((entry: CaseValueDrawerEntry) => {
    const previous = activeRef.current
    activeRef.current = entry
    setActive(entry)
    if (previous && previous.id !== entry.id) {
      previous.onClose()
    }
  }, [])

  const release = useCallback((id: string) => {
    if (activeRef.current?.id !== id) return
    activeRef.current = null
    setActive(null)
  }, [])

  const value = useMemo(
    () => ({ activeId: active?.id ?? null, bodyElement, claim, release }),
    [active?.id, bodyElement, claim, release]
  )
  const shown = active ?? lastActiveRef.current

  return (
    <CaseValueDrawerContext.Provider value={value}>
      {children}
      <CaseValueDrawerShell
        open={active !== null}
        onClose={() => activeRef.current?.onClose()}
        title={shown?.title ?? ""}
        description={shown?.description}
        size={shown?.size}
        bodyRef={setBodyElement}
      />
    </CaseValueDrawerContext.Provider>
  )
}

/** Props for {@link CaseValueDrawer}. */
export interface CaseValueDrawerProps {
  open: boolean
  /** Called with `false` when the drawer is closed or another value takes it. */
  onOpenChange: (open: boolean) => void
  /** Heading: the field or column the value belongs to. */
  title: string
  /** Optional line under the heading. */
  description?: string
  /** Drawer width; defaults to `default`. */
  size?: CaseValueDrawerSize
  /**
   * The viewer or editor. It fills the drawer below the header, so lay it out
   * as a full-height column with any footer pinned and the body scrolling.
   */
  children: ReactNode
}

/**
 * Opens a long text or JSON value in the case page's left drawer, which is
 * non-modal so the rest of the page keeps working while the value is open.
 *
 * Under a {@link CaseValueDrawerProvider} every instance shares one drawer and
 * the latest to open wins. The content still renders from this component's
 * place in the tree, so it keeps the contexts and props around it. Without a
 * provider it falls back to a drawer of its own.
 */
export function CaseValueDrawer({
  open,
  onOpenChange,
  title,
  description,
  size,
  children,
}: CaseValueDrawerProps) {
  const id = useId()
  const context = useContext(CaseValueDrawerContext)
  const claim = context?.claim
  const release = context?.release
  const onOpenChangeRef = useRef(onOpenChange)
  onOpenChangeRef.current = onOpenChange
  const handleClose = useCallback(() => onOpenChangeRef.current(false), [])

  useEffect(() => {
    if (!claim || !open) return
    claim({ id, title, description, size, onClose: handleClose })
  }, [claim, open, id, title, description, size, handleClose])

  useEffect(() => {
    if (!release || !open) return
    return () => release(id)
  }, [release, open, id])

  if (!context) {
    return (
      <CaseValueDrawerShell
        open={open}
        onClose={handleClose}
        title={title}
        description={description}
        size={size}
      >
        {children}
      </CaseValueDrawerShell>
    )
  }

  if (!open || context.activeId !== id || !context.bodyElement) {
    return null
  }
  return createPortal(children, context.bodyElement)
}
