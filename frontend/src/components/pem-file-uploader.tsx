"use client"

import { File as FileIcon, Upload } from "lucide-react"
import {
  type ChangeEvent,
  type DragEvent,
  useCallback,
  useMemo,
  useRef,
  useState,
} from "react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

interface PemFileUploaderProps {
  onValueLoaded: (value: string) => void
  onError: (message: string) => void
  onClearError: () => void
  allowedExtensions?: string[]
  chooseLabel?: string
  className?: string
}

export function PemFileUploader({
  onValueLoaded,
  onError,
  onClearError,
  allowedExtensions = [".pem"],
  chooseLabel = "Choose PEM",
  className,
}: PemFileUploaderProps) {
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const [fileName, setFileName] = useState<string | null>(null)

  const normalizedExtensions = useMemo(
    () => allowedExtensions.map((ext) => ext.toLowerCase()),
    [allowedExtensions]
  )
  const acceptedFileTypes = normalizedExtensions.join(",")
  const allowedExtensionsText = normalizedExtensions.join(", ")

  const resetInput = useCallback(() => {
    if (fileInputRef.current) {
      fileInputRef.current.value = ""
    }
  }, [])

  const handleFile = useCallback(
    (file: File | undefined) => {
      if (!file) {
        return
      }

      const lowercaseName = file.name.toLowerCase()
      const isAllowed = normalizedExtensions.some((ext) =>
        lowercaseName.endsWith(ext)
      )
      if (!isAllowed) {
        onError(`Upload a supported file (${allowedExtensionsText}).`)
        resetInput()
        return
      }

      const reader = new FileReader()
      reader.onload = () => {
        const text = typeof reader.result === "string" ? reader.result : ""
        if (text.trim().length === 0) {
          onError("Uploaded file is empty.")
          resetInput()
          return
        }
        onValueLoaded(text)
        onClearError()
        setFileName(file.name)
        resetInput()
      }
      reader.onerror = () => {
        onError("Failed to read the uploaded file.")
        resetInput()
      }
      reader.readAsText(file)
    },
    [
      allowedExtensionsText,
      normalizedExtensions,
      onClearError,
      onError,
      onValueLoaded,
      resetInput,
    ]
  )

  const handleInputChange = useCallback(
    (event: ChangeEvent<HTMLInputElement>) => {
      handleFile(event.target.files?.[0])
    },
    [handleFile]
  )

  const handleDrop = useCallback(
    (event: DragEvent<HTMLDivElement>) => {
      event.preventDefault()
      handleFile(event.dataTransfer.files?.[0])
      event.dataTransfer.clearData()
    },
    [handleFile]
  )

  const handleDragOver = useCallback((event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    event.dataTransfer.dropEffect = "copy"
  }, [])

  return (
    <>
      <input
        ref={fileInputRef}
        type="file"
        accept={acceptedFileTypes}
        className="hidden"
        onChange={handleInputChange}
      />
      <div
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        className={cn(
          "flex items-center justify-between gap-3 rounded-md border border-dashed bg-muted/30 px-3 py-2",
          className
        )}
      >
        <p className="min-w-0 text-xs text-muted-foreground">
          {fileName ? (
            <span className="inline-flex items-center gap-1 text-foreground">
              <FileIcon className="h-3.5 w-3.5" />
              <span className="truncate">{fileName}</span>
            </span>
          ) : (
            <>Drop file here ({allowedExtensionsText})</>
          )}
        </p>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => fileInputRef.current?.click()}
        >
          <Upload className="mr-2 h-3.5 w-3.5" />
          {fileName ? "Replace file" : chooseLabel}
        </Button>
      </div>
    </>
  )
}
