"use client"

import { Maximize2, X } from "lucide-react"
import Link from "next/link"
import { useCallback, useEffect, useRef } from "react"
import type { CaseReadMinimal } from "@/client"
import { CasePanelView } from "@/components/cases/case-panel-view"
import { Button } from "@/components/ui/button"
import {
  Drawer,
  DrawerClose,
  DrawerContent,
  DrawerDescription,
  DrawerFooter,
  DrawerHeader,
  DrawerTitle,
} from "@/components/ui/drawer"
import { Kbd } from "@/components/ui/kbd"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { caseHref } from "@/lib/cases/urls"

/** Load the next page once navigation gets this close to the loaded end. */
const LOAD_MORE_THRESHOLD = 3

/** Props for {@link CaseSidePanel}. */
export interface CaseSidePanelProps {
  /** The case shown in the panel; `null` keeps the panel closed. */
  caseId: string | null
  /** Loaded cases in the order the list shows them, used for up/down. */
  cases: CaseReadMinimal[]
  workspaceId: string
  onCaseChange: (caseId: string) => void
  onClose: () => void
  hasNextPage?: boolean
  isFetchingNextPage?: boolean
  onLoadMore?: () => void
}

/**
 * Returns `true` when an arrow key on `target` belongs to something else:
 * typing, an open menu or listbox, or a modal dialog layered over the page.
 */
function shouldIgnoreArrowKey(target: EventTarget | null): boolean {
  if (target instanceof HTMLElement) {
    const tagName = target.tagName
    if (
      target.isContentEditable ||
      tagName === "INPUT" ||
      tagName === "TEXTAREA" ||
      tagName === "SELECT"
    ) {
      return true
    }
    if (
      target.closest(
        "[contenteditable='true'], [role='textbox'], [role='combobox'], [role='listbox'], [role='menu'], [role='menubar'], [role='tablist'], [role='radiogroup'], [role='slider'], [role='tree'], [role='grid'], [data-radix-popper-content-wrapper]"
      )
    ) {
      return true
    }
  }
  return Boolean(
    document.querySelector(
      "[role='dialog'][data-state='open'], [role='alertdialog'][data-state='open'], [role='menu'][data-state='open']"
    )
  )
}

/**
 * Non-modal right drawer showing a case in the slim (embedded) case view.
 * The list behind it stays interactive; up/down (buttons or arrow keys) step
 * through the loaded cases in list order.
 */
export function CaseSidePanel({
  caseId,
  cases,
  workspaceId,
  onCaseChange,
  onClose,
  hasNextPage = false,
  isFetchingNextPage = false,
  onLoadMore,
}: CaseSidePanelProps) {
  const open = caseId !== null
  const foundIndex = caseId
    ? cases.findIndex((caseData) => caseData.id === caseId)
    : -1
  // When the open case leaves the list (status change into a filtered-out
  // status, deletion), keep stepping from where it was.
  const lastIndexRef = useRef(0)
  if (foundIndex >= 0) {
    lastIndexRef.current = foundIndex
  }
  const anchorIndex = foundIndex >= 0 ? foundIndex : lastIndexRef.current
  const previousCase = anchorIndex > 0 ? cases[anchorIndex - 1] : undefined
  const nextCase =
    foundIndex >= 0 ? cases[foundIndex + 1] : cases[lastIndexRef.current]
  const currentCase = foundIndex >= 0 ? cases[foundIndex] : undefined

  useEffect(() => {
    if (
      open &&
      hasNextPage &&
      !isFetchingNextPage &&
      onLoadMore &&
      anchorIndex >= cases.length - LOAD_MORE_THRESHOLD
    ) {
      onLoadMore()
    }
  }, [
    open,
    hasNextPage,
    isFetchingNextPage,
    onLoadMore,
    anchorIndex,
    cases.length,
  ])

  const goPrevious = useCallback(() => {
    if (previousCase) {
      onCaseChange(previousCase.id)
    }
  }, [previousCase, onCaseChange])

  const goNext = useCallback(() => {
    if (nextCase) {
      onCaseChange(nextCase.id)
    }
  }, [nextCase, onCaseChange])

  useEffect(() => {
    if (!open) {
      return
    }
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key !== "ArrowUp" && event.key !== "ArrowDown") {
        return
      }
      if (
        event.defaultPrevented ||
        event.altKey ||
        event.ctrlKey ||
        event.metaKey ||
        event.shiftKey ||
        shouldIgnoreArrowKey(event.target)
      ) {
        return
      }
      event.preventDefault()
      if (event.key === "ArrowUp") {
        goPrevious()
      } else {
        goNext()
      }
    }
    window.addEventListener("keydown", handleKeyDown)
    return () => window.removeEventListener("keydown", handleKeyDown)
  }, [open, goPrevious, goNext])

  return (
    <Drawer
      open={open}
      onOpenChange={(nextOpen) => {
        if (!nextOpen) {
          onClose()
        }
      }}
      modal={false}
      disablePointerDismissal
      swipeDirection="right"
    >
      <DrawerContent
        className="data-[swipe-direction=right]:w-full data-[swipe-direction=right]:sm:w-[40rem] data-[swipe-direction=right]:sm:max-w-[calc(100vw-3rem)]"
        initialFocus={false}
      >
        <DrawerHeader className="flex-row items-center justify-between gap-2 border-b px-3 py-2">
          <div className="flex min-w-0 items-center gap-2">
            <DrawerTitle className="shrink-0 text-xs font-medium text-muted-foreground">
              {currentCase?.short_id ?? "Case"}
            </DrawerTitle>
            <DrawerDescription className="truncate text-xs text-foreground">
              {currentCase?.summary}
            </DrawerDescription>
          </div>
          <TooltipProvider delayDuration={300}>
            <div className="flex shrink-0 items-center gap-0.5">
              {caseId && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      asChild
                      variant="ghost"
                      size="icon"
                      className="size-7"
                    >
                      <Link
                        href={caseHref(workspaceId, caseId)}
                        aria-label="Open full page"
                      >
                        <Maximize2 className="size-3.5" />
                      </Link>
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>Open full page</TooltipContent>
                </Tooltip>
              )}
              <DrawerClose
                render={
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-7"
                    aria-label="Close side panel"
                  />
                }
              >
                <X className="size-3.5" />
              </DrawerClose>
            </div>
          </TooltipProvider>
        </DrawerHeader>
        <div className="min-h-0 flex-1 overflow-hidden">
          {caseId && <CasePanelView caseId={caseId} embedded />}
        </div>
        <DrawerFooter className="flex-row items-center justify-between gap-2 border-t px-3 py-2">
          <span className="flex items-center gap-1 text-xs text-muted-foreground">
            <Kbd>↑</Kbd>
            <Kbd>↓</Kbd>
            <span>to navigate</span>
          </span>
          <div className="flex items-center gap-3 text-xs text-muted-foreground">
            {foundIndex >= 0 && (
              <span className="tabular-nums">
                {foundIndex + 1} of {cases.length}
                {hasNextPage ? "+" : ""}
              </span>
            )}
            <span className="flex items-center gap-1">
              <Kbd>Esc</Kbd>
              <span>to close</span>
            </span>
          </div>
        </DrawerFooter>
      </DrawerContent>
    </Drawer>
  )
}
