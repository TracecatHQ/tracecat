"use client"

import { ExternalLink } from "lucide-react"
import { useCallback, useEffect, useState } from "react"
import { CaseDescriptionEditor } from "@/components/cases/case-description-editor"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

// -- Long text dialog --

interface LongTextFieldDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  fieldLabel: string
  initialValue: string
  onSave: (value: string) => void
}

/**
 * Dialog for editing a LONG_TEXT case field using the rich-text editor.
 */
export function LongTextFieldDialog({
  open,
  onOpenChange,
  fieldLabel,
  initialValue,
  onSave,
}: LongTextFieldDialogProps) {
  const [draft, setDraft] = useState(initialValue)

  useEffect(() => {
    if (open) {
      setDraft(initialValue)
    }
  }, [open, initialValue])

  const handleSave = useCallback(() => {
    onSave(draft)
    onOpenChange(false)
  }, [draft, onSave, onOpenChange])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{fieldLabel}</DialogTitle>
        </DialogHeader>
        <div className="min-h-[200px]">
          <CaseDescriptionEditor initialContent={draft} onChange={setDraft} />
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handleSave}>Save</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// -- URL dialog --

interface UrlFieldValue {
  url: string
  label: string
}

interface UrlFieldDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  fieldLabel: string
  initialValue: UrlFieldValue
  onSave: (value: UrlFieldValue) => void
}

/**
 * Dialog for editing a URL case field (url + label inputs).
 */
export function UrlFieldDialog({
  open,
  onOpenChange,
  fieldLabel,
  initialValue,
  onSave,
}: UrlFieldDialogProps) {
  const [url, setUrl] = useState(initialValue.url)
  const [label, setLabel] = useState(initialValue.label)

  useEffect(() => {
    if (open) {
      setUrl(initialValue.url)
      setLabel(initialValue.label)
    }
  }, [open, initialValue])

  const handleSave = useCallback(() => {
    onSave({ url: url.trim(), label: label.trim() })
    onOpenChange(false)
  }, [url, label, onSave, onOpenChange])

  const trimmedUrl = url.trim()
  const trimmedLabel = label.trim()
  const urlHint = getUrlHint(trimmedUrl)
  const isEmpty = trimmedUrl.length === 0 && trimmedLabel.length === 0
  const isValid = !urlHint && trimmedUrl.length > 0 && trimmedLabel.length > 0

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[425px]">
        <DialogHeader>
          <DialogTitle>{fieldLabel}</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="url-field-label">Label</Label>
            <Input
              id="url-field-label"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="Display text"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="url-field-url">URL</Label>
            <Input
              id="url-field-url"
              type="url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://example.com"
            />
            {!isEmpty && urlHint && (
              <p className="text-xs text-muted-foreground">{urlHint}</p>
            )}
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handleSave} disabled={!isValid}>
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// -- Inline renderers for the case panel --

interface LongTextFieldCellProps {
  onClick: () => void
  hasValue: boolean
}

/**
 * Inline cell for a LONG_TEXT field: shows an "Expand" button.
 */
export function LongTextFieldCell({
  onClick,
  hasValue,
}: LongTextFieldCellProps) {
  return (
    <Button
      variant="ghost"
      size="sm"
      className="h-7 w-full justify-end px-2 text-sm font-normal text-muted-foreground"
      onClick={onClick}
    >
      {hasValue ? "Expand" : "Add..."}
    </Button>
  )
}

interface UrlFieldCellProps {
  value: UrlFieldValue | null
  onEdit: () => void
}

/**
 * Inline cell for a URL field: shows label text and an external-link icon.
 */
export function UrlFieldCell({ value, onEdit }: UrlFieldCellProps) {
  if (!value || !value.url) {
    return (
      <Button
        variant="ghost"
        size="sm"
        className="h-7 w-full justify-end px-2 text-sm font-normal text-muted-foreground"
        onClick={onEdit}
      >
        Add...
      </Button>
    )
  }

  return (
    <div className="flex h-7 w-full items-center justify-end gap-1">
      <button
        type="button"
        className="min-w-0 truncate text-right text-sm hover:underline"
        onClick={onEdit}
        title={value.label}
      >
        {value.label}
      </button>
      {isSafeUrl(value.url) && (
        <a
          href={value.url}
          target="_blank"
          rel="noopener noreferrer"
          className="shrink-0 text-muted-foreground hover:text-foreground"
          title={value.url}
          onClick={(e) => e.stopPropagation()}
        >
          <ExternalLink className="size-3.5" />
        </a>
      )}
    </div>
  )
}

/**
 * Return true when the URL is a valid absolute http or https URL.
 * Used to gate the rendered anchor so non-http(s) schemes (e.g. javascript:)
 * are never placed in an href attribute.
 */
function isSafeUrl(url: string): boolean {
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
function getUrlHint(url: string): string | undefined {
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
