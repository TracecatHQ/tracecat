"use client"

import type { EditorView } from "@codemirror/view"
import { ImageIcon } from "lucide-react"
import { type ChangeEvent, useRef, useState } from "react"
import { Button } from "@/components/ui/button"
import { toast } from "@/components/ui/use-toast"
import {
  FORM_IMAGE_TYPES,
  type FormImageEncoder,
  FormImageError,
  prepareFormImage,
} from "@/lib/forms/image-data-uri"

/**
 * Put a data URI into the editor at the cursor, replacing any selection.
 *
 * The URI is wrapped in double quotes, because a comma ends a plain YAML
 * scalar inside `{...}`. If the cursor already sits after a quote, the
 * author has typed them and the URI goes in bare.
 */
export function insertImageDataUri(view: EditorView, dataUri: string): void {
  const { from } = view.state.selection.main
  const before = view.state.sliceDoc(Math.max(0, from - 1), from)
  const text = before === '"' || before === "'" ? dataUri : `"${dataUri}"`
  view.dispatch(view.state.replaceSelection(text))
  view.focus()
}

/**
 * Pick a PNG, JPEG or WebP image, shrink it and insert it into the config
 * as an inline `data:` URI, for the `image` of a card option.
 */
export function InsertImageButton({
  view,
  disabled = false,
  encode,
}: {
  /** The config editor. The button is inert until it exists. */
  view: EditorView | null
  disabled?: boolean
  /** Replaces the canvas resize. For tests. */
  encode?: FormImageEncoder
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [isWorking, setIsWorking] = useState(false)

  async function handleFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    // Clear the input so picking the same file again fires a change.
    event.target.value = ""
    if (!file || !view) {
      return
    }
    setIsWorking(true)
    try {
      insertImageDataUri(view, await prepareFormImage(file, encode))
    } catch (error) {
      toast({
        title: "Couldn't insert image",
        description:
          error instanceof FormImageError
            ? error.message
            : "Something went wrong. Try another image.",
        variant: "destructive",
      })
    } finally {
      setIsWorking(false)
    }
  }

  return (
    <>
      <input
        ref={inputRef}
        type="file"
        accept={FORM_IMAGE_TYPES.join(",")}
        className="hidden"
        aria-label="Image file"
        onChange={handleFile}
      />
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="h-7 gap-1.5 px-2 text-xs"
        disabled={disabled || isWorking || !view}
        onClick={() => inputRef.current?.click()}
      >
        <ImageIcon className="size-3.5" />
        Insert image
      </Button>
    </>
  )
}
