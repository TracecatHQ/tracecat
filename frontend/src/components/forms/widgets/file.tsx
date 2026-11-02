"use client"

import { FileIcon, UploadIcon, XIcon } from "lucide-react"
import { type DragEvent, useRef, useState } from "react"
import { formStyles } from "@/components/forms/form-styles"
import { controlProps, type FormWidgetProps } from "@/components/forms/widgets"
import { Button } from "@/components/ui/button"
import type { FormFileItem } from "@/lib/forms/values"
import { cn, formatFileSize } from "@/lib/utils"

/** Files a list field takes when the form sets no `max_files`. */
const DEFAULT_MAX_FILES = 10

let nextFileKey = 0

function describeLimits(accept: string[], maxFiles: number): string {
  const count = maxFiles === 1 ? "One file" : `Up to ${maxFiles} files`
  return accept.length > 0 ? `${accept.join(", ")}. ${count}.` : `${count}.`
}

function toFileItems(value: unknown): FormFileItem[] {
  return (Array.isArray(value) ? value : []) as FormFileItem[]
}

function statusLabel(item: FormFileItem): string | null {
  if (item.status === "uploading") return "Uploading..."
  if (item.status === "failed") return item.error ?? "Upload failed"
  return null
}

/**
 * File picker with a drop zone and a list of picked files.
 *
 * A file over the field's size limit is listed as `failed` and never sent.
 * The `accept` list filters the picker; the server checks the type of a
 * dropped file. The rest upload when the renderer passes `uploadFile`: each
 * moves from `uploading` to `uploaded` or `failed`, and a failure shows the
 * server's reason. Without an uploader, as in preview, files stay `selected`
 * and nothing leaves the browser.
 */
export function FileWidget(props: FormWidgetProps) {
  const { field, value, updateValue, disabled, invalid, uploadFile } = props
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const items = toFileItems(value)

  const accept = field.config?.accept ?? []
  const maxSize = field.config?.max_file_size
  const maxFiles = field.multiple
    ? (field.config?.max_files ?? DEFAULT_MAX_FILES)
    : 1

  // Uploads finish out of order, and can outlive this widget when its field
  // is hidden and shown again, so every change applies to the list in form
  // state at that moment, not the one this render saw.
  function update(change: (current: FormFileItem[]) => FormFileItem[]) {
    updateValue((current) => change(toFileItems(current)))
  }

  function patch(key: string, changes: Partial<FormFileItem>) {
    update((current) =>
      current.map((item) => (item.key === key ? { ...item, ...changes } : item))
    )
  }

  function addFiles(files: File[]) {
    // A single-file field replaces its file; a list field fills what is left.
    const kept = field.multiple ? items : []
    const picked = files.slice(0, Math.max(maxFiles - kept.length, 0))
    if (picked.length === 0) return
    const added = picked.map((file) => {
      const item: FormFileItem = {
        key: `file-${++nextFileKey}`,
        name: file.name,
        size: file.size,
        status: uploadFile ? "uploading" : "selected",
      }
      if (typeof maxSize === "number" && file.size > maxSize) {
        item.status = "failed"
        item.error = `Files can be at most ${formatFileSize(maxSize)}`
      }
      return { file, item }
    })
    const addedItems = added.map(({ item }) => item)
    update((current) => [...(field.multiple ? current : []), ...addedItems])
    for (const { file, item } of added) {
      if (!uploadFile || item.status !== "uploading") continue
      uploadFile({
        workspaceId: props.workspaceId,
        formId: props.formId,
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
    if (!disabled) addFiles(Array.from(event.dataTransfer.files))
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
            {...controlProps(props)}
            type="button"
            disabled={disabled || full}
            onClick={() => inputRef.current?.click()}
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
                  update((current) =>
                    current.filter(({ key }) => key !== item.key)
                  )
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
