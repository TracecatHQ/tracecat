"use client"

import { FileIcon, UploadIcon, XIcon } from "lucide-react"
import { type DragEvent, useRef, useState } from "react"
import { formStyles } from "@/components/forms/form-styles"
import {
  describedBy,
  type FormWidgetProps,
} from "@/components/forms/widgets/types"
import { Button } from "@/components/ui/button"
import { checkFormFile } from "@/lib/forms/files"
import type { FormFileItem } from "@/lib/forms/visibility"
import { cn, formatFileSize } from "@/lib/utils"

/** Files a list field takes when the form sets no `max_files`. */
const DEFAULT_MAX_FILES = 10

let nextFileKey = 0

function describeLimits(accept: string[], maxFiles: number): string {
  const count = maxFiles === 1 ? "One file" : `Up to ${maxFiles} files`
  return accept.length > 0 ? `${accept.join(", ")}. ${count}.` : `${count}.`
}

function statusLabel(item: FormFileItem): string | null {
  if (item.status === "uploading") {
    return "Uploading..."
  }
  if (item.status === "failed") {
    return item.error ?? "Upload failed"
  }
  return null
}

/**
 * File picker with a drop zone and a list of picked files.
 *
 * A picked file is first checked against the field's `accept` and size
 * limit; one that fails is listed as `failed` with the reason and is never
 * sent. The rest upload only when the form runs live and the renderer was
 * given `runtime.uploadFile`: each moves from `uploading` to `uploaded` or
 * `failed`, and a failure shows the server's reason. In preview, and without
 * an uploader, files stay `selected` and nothing leaves the browser.
 */
export function FileWidget({
  id,
  field,
  value,
  onChange,
  onBlur,
  disabled,
  invalid,
  runtime,
}: FormWidgetProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const items = (Array.isArray(value) ? value : []) as FormFileItem[]
  // Uploads finish out of order and after re-renders, so each completion
  // must patch the newest list, not the one captured when it started.
  const itemsRef = useRef(items)
  itemsRef.current = items

  const accept = field.config?.accept ?? []
  const maxFiles = field.multiple
    ? (field.config?.max_files ?? DEFAULT_MAX_FILES)
    : 1
  const upload = runtime.mode === "live" ? runtime.uploadFile : undefined

  function commit(next: FormFileItem[]) {
    itemsRef.current = next
    onChange(next)
  }

  function patch(key: string, changes: Partial<FormFileItem>) {
    commit(
      itemsRef.current.map((item) =>
        item.key === key ? { ...item, ...changes } : item
      )
    )
  }

  function addFiles(files: File[]) {
    // A single-file field replaces its file; a list field fills what is left.
    const kept = field.multiple ? itemsRef.current : []
    const picked = files.slice(0, Math.max(maxFiles - kept.length, 0))
    if (picked.length === 0) {
      return
    }
    const added = picked.map((file) => {
      const error = checkFormFile(file, field.config)
      const item: FormFileItem = {
        key: `file-${++nextFileKey}`,
        name: file.name,
        size: file.size,
        status: upload ? "uploading" : "selected",
      }
      if (error) {
        item.status = "failed"
        item.error = error
      }
      return { file, item }
    })
    commit([...kept, ...added.map(({ item }) => item)])
    if (!upload) {
      return
    }
    for (const { file, item } of added) {
      if (item.status !== "uploading") {
        continue
      }
      upload({
        workspaceId: runtime.workspaceId,
        formId: runtime.formId,
        field: field.name,
        file,
      }).then(
        ({ id: fileId }) => patch(item.key, { status: "uploaded", fileId }),
        (error: unknown) =>
          patch(item.key, {
            status: "failed",
            error: error instanceof Error ? error.message : "Upload failed",
          })
      )
    }
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault()
    setDragging(false)
    if (!disabled) {
      addFiles(Array.from(event.dataTransfer.files))
    }
  }

  const full = field.multiple && items.length >= maxFiles

  return (
    <div className="flex flex-col gap-2">
      <div
        onDragOver={(event) => {
          event.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
        className={cn(
          "flex flex-col items-center gap-1 rounded-md border border-dashed border-muted-foreground/50 p-5 text-center text-sm leading-5 text-muted-foreground",
          dragging && "border-primary bg-primary/5",
          invalid && "border-destructive",
          disabled && "opacity-50"
        )}
      >
        <UploadIcon className="size-5" />
        <span>
          <button
            id={id}
            type="button"
            disabled={disabled || full}
            onClick={() => inputRef.current?.click()}
            onBlur={onBlur}
            aria-invalid={invalid}
            aria-describedby={describedBy(id, field, invalid)}
            className="rounded-sm font-medium text-foreground underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:no-underline"
          >
            {maxFiles === 1 ? "Choose a file" : "Choose files"}
          </button>{" "}
          or drag {maxFiles === 1 ? "it" : "them"} here
        </span>
        <span className={formStyles.help}>
          {describeLimits(accept, maxFiles)}
        </span>
        <input
          ref={inputRef}
          type="file"
          hidden
          tabIndex={-1}
          aria-label={field.label}
          multiple={maxFiles > 1}
          accept={accept.join(",") || undefined}
          disabled={disabled}
          onChange={(event) => {
            addFiles(Array.from(event.target.files ?? []))
            // Let the same file be picked again after it is removed.
            event.target.value = ""
          }}
        />
      </div>
      {items.length > 0 && (
        <ul className="flex flex-col gap-2">
          {items.map((item) => (
            <li
              key={item.key}
              className={cn(
                "flex min-h-11 items-center gap-2.5 rounded-md border py-1 pl-3 pr-1.5 text-sm",
                item.status === "failed" && "border-destructive"
              )}
            >
              <FileIcon className="size-4 shrink-0 text-muted-foreground" />
              <span className="truncate">{item.name}</span>
              <span className={cn(formStyles.help, "shrink-0")}>
                {formatFileSize(item.size)}
              </span>
              {statusLabel(item) && (
                <span
                  role={item.status === "failed" ? "alert" : "status"}
                  className={cn(
                    formStyles.help,
                    "min-w-0",
                    item.status === "failed" && "text-destructive"
                  )}
                >
                  {statusLabel(item)}
                </span>
              )}
              <Button
                type="button"
                variant="ghost"
                size="icon"
                disabled={disabled}
                aria-label={`Remove ${item.name}`}
                onClick={() =>
                  commit(itemsRef.current.filter(({ key }) => key !== item.key))
                }
                className="ml-auto size-8 shrink-0"
              >
                <XIcon className="size-4" />
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
