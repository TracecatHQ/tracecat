"use client"

import React from "react"
import { useFormContext } from "react-hook-form"
import { z } from "zod"

import { CsvPreviewData } from "@/lib/tables"
import { Button } from "@/components/ui/button"
import {
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Spinner } from "@/components/loading/spinner"

// Shared constants
export const CSV_CONSTANTS = {
  BYTES_PER_MB: 1024 * 1024,
  FILE_SIZE_LIMIT_MB: 5,
}

// Common validation function for CSV file
export const validateCsvFile = (file: File) => {
  // Check file size
  if (
    file.size >
    CSV_CONSTANTS.FILE_SIZE_LIMIT_MB * CSV_CONSTANTS.BYTES_PER_MB
  ) {
    return false
  }
  // Check file type
  return file.type === "text/csv" || file.name.endsWith(".csv")
}

// Common validation message generator
export const getCsvValidationMessage = (file: File) => {
  return `Please upload a CSV file under ${CSV_CONSTANTS.FILE_SIZE_LIMIT_MB}MB. Current file size: ${(file.size / CSV_CONSTANTS.BYTES_PER_MB).toFixed(2)}MB`
}

// Interface for the CsvUploadForm props
export interface CsvUploadFormProps {
  isUploading: boolean
  nextPage: () => void
  formFieldName?: string // Allow customizing the form field name
}

// Shared CsvUploadForm component
export function CsvUploadForm({
  isUploading,
  nextPage,
  formFieldName = "file",
}: CsvUploadFormProps) {
  const form = useFormContext()

  return (
    <div className="space-y-4">
      <FormField
        control={form.control}
        name={formFieldName}
        render={({ field }) => (
          <FormItem>
            <FormLabel>CSV file</FormLabel>
            <FormControl>
              <Input
                type="file"
                accept=".csv"
                onChange={(e) => {
                  const file = e.target.files?.[0]
                  if (file) {
                    field.onChange(file)
                  }
                }}
              />
            </FormControl>
            <FormDescription>
              Upload file (max {CSV_CONSTANTS.FILE_SIZE_LIMIT_MB}MB)
            </FormDescription>
            <FormMessage />
          </FormItem>
        )}
      />
      <Button
        onClick={async () => {
          const isValid = await form.trigger(formFieldName)
          if (!isValid) return
          nextPage()
        }}
        disabled={isUploading}
      >
        {isUploading ? (
          <>
            <Spinner className="mr-2" />
            Uploading...
          </>
        ) : (
          "Next"
        )}
      </Button>
    </div>
  )
}

// Interface for the CsvPreview props
export interface CsvPreviewProps {
  csvData: CsvPreviewData
}

// Shared CsvPreview component
//NOTE: We use parseCellValue twice but it's ok because there are only 5 rows and optimizing this is not worth the complexity
type CsvRowData = Record<string, string | number | boolean | null | object>

export function CsvPreview({ csvData }: CsvPreviewProps) {
  const parseCellValue = (value: unknown) => {
    const isObject = typeof value === "object" && value !== null
    let stringified = ""

    if (isObject) {
      stringified = JSON.stringify(value)
    }

    return {
      isObject,
      stringified,
      displayString: isObject ? stringified : String(value || ""),
    }
  }

  const getDisplayValue = (row: CsvRowData, header: string) => {
    const cellValue = row[header]
    const { isObject, stringified } = parseCellValue(cellValue)

    if (isObject) {
      if (stringified.length > 30) {
        return stringified.substring(0, 27) + "..."
      } else {
        return stringified
      }
    } else {
      return String(cellValue || "")
    }
  }

  const getCellTitle = (row: CsvRowData, header: string) => {
    const cellValue = row[header]
    const { isObject, stringified, displayString } = parseCellValue(cellValue)

    if (isObject) {
      return stringified
    } else {
      return displayString
    }
  }

  return (
    <div className="space-y-4">
      <div className="text-sm font-medium">Preview (first 5 rows)</div>
      <div className="max-h-60 overflow-auto rounded border">
        <Table className="min-w-full table-fixed">
          <TableHeader>
            <TableRow>
              {csvData.headers.map((header) => (
                <TableHead
                  key={header}
                  className="sticky top-0 min-w-[160px] whitespace-nowrap bg-muted/50"
                >
                  {header}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {csvData.preview.map((row, i) => (
              <TableRow key={i}>
                {csvData.headers.map((header) => (
                  <TableCell
                    key={header}
                    className="min-w-[160px] truncate"
                    title={getCellTitle(row, header)}
                  >
                    {getDisplayValue(row, header)}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}

// Reusable schema parts for CSV files
export const csvFileSchema = z
  .instanceof(File)
  .refine(validateCsvFile, (file) => ({
    message: getCsvValidationMessage(file),
  }))
