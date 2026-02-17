"use client"

import { File as FileIcon, Upload, X } from "lucide-react"
import {
  type ChangeEvent,
  type DragEvent,
  useCallback,
  useRef,
  useState,
} from "react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

interface ServiceAccountJsonUploaderProps {
  value: string
  onChange: (value: string) => void
  onError: (message: string) => void
  onClearError: () => void
  placeholder: string
  existingConfigured: boolean
  hasError?: boolean
  onDetectedEmail?: (email: string | null) => void
}

export function ServiceAccountJsonUploader({
  value,
  onChange,
  onError,
  onClearError,
  placeholder,
  existingConfigured,
  hasError = false,
  onDetectedEmail,
}: ServiceAccountJsonUploaderProps) {
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const [fileName, setFileName] = useState<string | null>(null)
  const [detectedEmail, setDetectedEmail] = useState<string | null>(null)

  const resetInput = useCallback(() => {
    if (fileInputRef.current) {
      fileInputRef.current.value = ""
    }
  }, [])

  const clearSelection = useCallback(() => {
    setFileName(null)
    setDetectedEmail(null)
    onDetectedEmail?.(null)
    onChange("")
    onClearError()
    resetInput()
  }, [onChange, onClearError, onDetectedEmail, resetInput])

  const handleFile = useCallback(
    (file: File | undefined) => {
      if (!file) {
        return
      }

      if (!file.name.toLowerCase().endsWith(".json")) {
        onChange("")
        setFileName(null)
        setDetectedEmail(null)
        onDetectedEmail?.(null)
        onError("Upload a .json file exported from Google Cloud.")
        resetInput()
        return
      }

      const reader = new FileReader()
      reader.onload = () => {
        const text = typeof reader.result === "string" ? reader.result : ""

        try {
          const parsed = JSON.parse(text)
          if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
            throw new Error("Uploaded key must be a JSON object.")
          }
          if (parsed.type !== "service_account") {
            throw new Error('JSON key must include "type": "service_account".')
          }
          if (
            typeof parsed.private_key !== "string" ||
            parsed.private_key.trim().length === 0
          ) {
            throw new Error("JSON key is missing a private_key.")
          }

          const normalized = JSON.stringify(parsed)
          onChange(normalized)
          setFileName(file.name)

          const email =
            typeof parsed.client_email === "string"
              ? parsed.client_email.trim()
              : ""
          const nextEmail = email || null
          setDetectedEmail(nextEmail)
          onDetectedEmail?.(nextEmail)

          onClearError()
        } catch (error) {
          onChange("")
          setFileName(null)
          setDetectedEmail(null)
          onDetectedEmail?.(null)
          const message =
            error instanceof Error
              ? error.message
              : "Failed to parse service account JSON key."
          onError(message)
        } finally {
          resetInput()
        }
      }
      reader.onerror = () => {
        onChange("")
        setFileName(null)
        setDetectedEmail(null)
        onDetectedEmail?.(null)
        onError("Failed to read the uploaded file.")
        resetInput()
      }

      reader.readAsText(file)
    },
    [onChange, onClearError, onDetectedEmail, onError, resetInput]
  )

  const handleInputChange = useCallback(
    (event: ChangeEvent<HTMLInputElement>) => {
      const file = event.target.files?.[0]
      handleFile(file)
    },
    [handleFile]
  )

  const handleDrop = useCallback(
    (event: DragEvent<HTMLDivElement>) => {
      event.preventDefault()
      const file = event.dataTransfer.files?.[0]
      handleFile(file)
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
        accept=".json,application/json"
        className="hidden"
        onChange={handleInputChange}
      />
      <div
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        className={cn(
          "flex flex-col gap-3 rounded-md border border-dashed p-4 transition-colors",
          hasError
            ? "border-destructive/80 bg-destructive/5"
            : "border-muted-foreground/30 bg-muted/40 hover:border-muted-foreground/50"
        )}
      >
        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-xs text-muted-foreground">
              {fileName ? (
                <span className="inline-flex items-center gap-1 text-sm font-medium text-foreground">
                  <FileIcon className="h-4 w-4" />
                  {fileName}
                </span>
              ) : (
                placeholder
              )}
            </p>
            <div className="flex items-center gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => fileInputRef.current?.click()}
              >
                <Upload className="mr-2 h-4 w-4" />
                {fileName ? "Replace file" : "Choose JSON"}
              </Button>
              {fileName && (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={clearSelection}
                >
                  <X className="mr-1 h-4 w-4" />
                  Remove
                </Button>
              )}
            </div>
          </div>
          {existingConfigured && !fileName && (
            <p className="text-xs text-muted-foreground">
              Existing key remains active until you provide a new file.
            </p>
          )}
          {detectedEmail && (
            <p className="text-xs text-muted-foreground">
              Detected service account:{" "}
              <span className="font-medium">{detectedEmail}</span>
            </p>
          )}
        </div>
      </div>
    </>
  )
}
