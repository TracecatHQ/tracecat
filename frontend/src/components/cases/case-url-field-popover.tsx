"use client"

import { CornerDownLeft, ExternalLink, Trash2 } from "lucide-react"
import { useCallback, useEffect, useRef, useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Popover, PopoverAnchor, PopoverContent } from "@/components/ui/popover"
import { Separator } from "@/components/ui/separator"
import { cn } from "@/lib/utils"

/** Stored shape of a URL case field value. */
export interface UrlFieldValue {
  url: string
  label: string
}

/**
 * Return true when the URL is a valid absolute http or https URL.
 * Used to gate link opening so non-http(s) schemes (e.g. javascript:)
 * are never navigated to.
 */
export function isSafeUrl(url: string): boolean {
  try {
    const parsed = new URL(url)
    return parsed.protocol === "http:" || parsed.protocol === "https:"
  } catch {
    return false
  }
}

/**
 * Return a user-friendly hint if the URL is not a valid absolute http(s) URL,
 * or undefined if the URL is valid. Returns undefined for empty strings so
 * the hint only shows once the user has started typing.
 */
export function getUrlHint(url: string): string | undefined {
  if (url.length === 0) return undefined
  try {
    const parsed = new URL(url)
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      return "URL must start with http:// or https://"
    }
    return undefined
  } catch {
    return "Enter a valid URL, e.g. https://example.com"
  }
}

interface UrlFieldPopoverProps {
  fieldId: string
  value: UrlFieldValue | null
  onSave: (value: UrlFieldValue | null) => void | Promise<void>
}

/**
 * Anchored popover editor for a URL case field, mirroring the editor's
 * compact link popover: label input on top, then a URL input with apply,
 * open-in-new-window, and remove actions. Outside clicks discard drafts.
 */
export function UrlFieldPopover({
  fieldId,
  value,
  onSave,
}: UrlFieldPopoverProps) {
  const [open, setOpen] = useState(false)
  const [labelDraft, setLabelDraft] = useState(value?.label ?? "")
  const [urlDraft, setUrlDraft] = useState(value?.url ?? "")
  const anchorRef = useRef<HTMLDivElement>(null)

  // Re-seed drafts from the saved value whenever the popover opens, so an
  // outside click discards unsaved drafts.
  useEffect(() => {
    if (open) {
      setLabelDraft(value?.label ?? "")
      setUrlDraft(value?.url ?? "")
    }
  }, [open, value?.label, value?.url])

  const trimmedLabel = labelDraft.trim()
  const trimmedUrl = urlDraft.trim()
  const urlHint = getUrlHint(trimmedUrl)
  const canApply = trimmedLabel.length > 0 && trimmedUrl.length > 0 && !urlHint
  const canOpen = isSafeUrl(trimmedUrl)

  const handleApply = useCallback(() => {
    if (!canApply) return
    void onSave({ url: trimmedUrl, label: trimmedLabel })
    setOpen(false)
  }, [canApply, onSave, trimmedUrl, trimmedLabel])

  const handleOpenLink = useCallback(() => {
    window.open(trimmedUrl, "_blank", "noopener,noreferrer")
  }, [trimmedUrl])

  const handleRemove = useCallback(() => {
    void onSave(null)
    setOpen(false)
  }, [onSave])

  const handleInputKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLInputElement>) => {
      if (event.key === "Enter") {
        event.preventDefault()
        handleApply()
      }
    },
    [handleApply]
  )

  /**
   * Open the popover on a plain left click only. Modifier and non-primary
   * clicks fall through to the browser so cmd/ctrl-click opens a new tab and
   * shift-click opens a new window.
   */
  const handleTriggerClick = useCallback(
    (event: React.MouseEvent<HTMLElement>) => {
      if (
        event.metaKey ||
        event.ctrlKey ||
        event.shiftKey ||
        event.altKey ||
        event.button !== 0
      ) {
        return
      }
      event.preventDefault()
      setOpen(true)
    },
    []
  )

  const displayText = value ? value.label || value.url : "Add..."
  const triggerClassName = cn(
    "min-w-0 truncate text-right text-sm",
    value
      ? "text-primary underline underline-offset-4"
      : "text-muted-foreground hover:underline"
  )

  function renderTrigger() {
    // Render a real anchor for safe URLs so the browser handles new-tab
    // clicks, the context menu, and the status-bar link preview natively.
    if (value && isSafeUrl(value.url)) {
      return (
        <a
          href={value.url}
          rel="noopener noreferrer"
          data-case-field-link=""
          className={triggerClassName}
          title={value.url}
          onClick={handleTriggerClick}
        >
          {displayText}
        </a>
      )
    }
    return (
      <button
        type="button"
        className={triggerClassName}
        title={value?.url}
        onClick={handleTriggerClick}
      >
        {displayText}
      </button>
    )
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverAnchor asChild>
        <div
          ref={anchorRef}
          className="flex h-7 w-full items-center justify-end"
        >
          {renderTrigger()}
        </div>
      </PopoverAnchor>
      <PopoverContent
        side="bottom"
        align="end"
        sideOffset={4}
        collisionPadding={8}
        onInteractOutside={(event) => {
          // A pointer or focus event on this popover's own row is not an
          // "outside" interaction. Without this the layer dismisses and the
          // row's click handler immediately reopens it, replaying the open
          // animation on every click.
          const target = event.detail.originalEvent.target
          if (target instanceof Node && anchorRef.current?.contains(target)) {
            event.preventDefault()
          }
        }}
        aria-label={`Edit ${fieldId} URL`}
        className="w-[min(24rem,calc(100vw-2rem))] space-y-1 p-1.5"
      >
        <Input
          placeholder="Display label"
          value={labelDraft}
          onChange={(event) => setLabelDraft(event.target.value)}
          onKeyDown={handleInputKeyDown}
          autoFocus
          autoComplete="off"
          className="h-7 text-sm"
        />
        <div className="flex items-center gap-0.5">
          <Input
            type="url"
            placeholder="https://example.com"
            value={urlDraft}
            onChange={(event) => setUrlDraft(event.target.value)}
            onKeyDown={handleInputKeyDown}
            autoComplete="off"
            className="h-7 text-sm"
          />
          <Button
            type="button"
            size="icon"
            variant="ghost"
            title="Apply"
            aria-label="Apply"
            disabled={!canApply}
            onClick={handleApply}
            className="size-7 shrink-0"
          >
            <CornerDownLeft className="size-3.5" />
          </Button>
          <Separator orientation="vertical" className="mx-0.5 h-4" />
          <Button
            type="button"
            size="icon"
            variant="ghost"
            title="Open in new window"
            aria-label="Open in new window"
            disabled={!canOpen}
            onClick={handleOpenLink}
            className="size-7 shrink-0"
          >
            <ExternalLink className="size-3.5" />
          </Button>
          <Button
            type="button"
            size="icon"
            variant="ghost"
            title="Remove URL"
            aria-label="Remove URL"
            onClick={handleRemove}
            className="size-7 shrink-0"
          >
            <Trash2 className="size-3.5" />
          </Button>
        </div>
        {urlHint && (
          <p className="px-1 text-xs text-muted-foreground">{urlHint}</p>
        )}
      </PopoverContent>
    </Popover>
  )
}
