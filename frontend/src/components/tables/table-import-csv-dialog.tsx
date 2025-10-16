"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import type { DialogProps } from "@radix-ui/react-dialog"
import { useParams } from "next/navigation"
import { useCallback, useEffect, useState } from "react"
import { FormProvider, useForm, useFormContext } from "react-hook-form"
import { z } from "zod"
import { ApiError, type TableRead } from "@/client"
import { SqlTypeDisplay } from "@/components/data-type/sql-type-display"
import { Spinner } from "@/components/loading/spinner"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
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
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { toast } from "@/components/ui/use-toast"
import type { SqlType } from "@/lib/data-type"
import type { TracecatApiError } from "@/lib/errors"
import { useGetTable, useImportCsv } from "@/lib/hooks"
import { type CsvPreviewData, getCsvPreview } from "@/lib/tables"
import { useWorkspaceId } from "@/providers/workspace-id"

const BYTES_PER_MB = 1024 * 1024
const FILE_SIZE_LIMIT_MB = 5

// Form schema for Csv import
const csvImportSchema = z.object({
  file: z.instanceof(File).refine(
    (file) => {
      // Check file size (5MB limit)
      if (file.size > FILE_SIZE_LIMIT_MB * BYTES_PER_MB) {
        return false
      }
      // Check file type
      return file.type === "text/csv" || file.name.endsWith(".csv")
    },
    (file) => ({
      message: `Please upload a CSV file under 5MB. Current file size: ${(file.size / BYTES_PER_MB).toFixed(2)}MB`,
    })
  ),
  columnMapping: z
    .record(z.string(), z.string())
    .refine(
      (mapping) => Object.keys(mapping).length > 0,
      "Please map at least one column"
    ),
})

type CsvImportFormValues = z.infer<typeof csvImportSchema>

interface TableImportCsvDialogProps extends DialogProps {
  open?: boolean
  onOpenChange?: (open: boolean) => void
}

export function TableImportCsvDialog({
  open,
  onOpenChange,
}: TableImportCsvDialogProps) {
  const params = useParams<{ tableId: string }>()
  const tableId = params?.tableId
  const workspaceId = useWorkspaceId()
  const { table } = useGetTable({
    tableId: tableId ?? "",
    workspaceId,
  })
  const { importCsv, importCsvIsPending } = useImportCsv()

  const [isUploading, setIsUploading] = useState(false)
  const [csvPreview, setCsvPreview] = useState<CsvPreviewData | null>(null)

  const form = useForm<CsvImportFormValues>({
    resolver: zodResolver(csvImportSchema),
    defaultValues: {
      columnMapping: {},
    },
  })

  // Reset state when dialog closes
  useEffect(() => {
    if (!open) {
      setCsvPreview(null)
      form.reset()
    }
  }, [open, form])

  const handleCreatePreview = useCallback(async () => {
    const { file } = form.getValues()
    try {
      setIsUploading(true)
      const parsedData = await getCsvPreview(file)
      setCsvPreview(parsedData)
    } catch (error) {
      console.error("Error parsing CSV preview:", error)
      toast({
        title: "Error",
        description: "Failed to parse CSV file",
      })
    } finally {
      setIsUploading(false)
    }
  }, [form])

  const onSubmit = async ({ file, columnMapping }: CsvImportFormValues) => {
    if (!csvPreview || !table) return

    try {
      const response = await importCsv({
        formData: {
          file,
          column_mapping: JSON.stringify(columnMapping),
        },
        tableId: tableId ?? "",
        workspaceId,
      })
      toast({
        title: "Import successful",
        description: `${response.rows_inserted} rows imported`,
      })
      onOpenChange?.(false)
    } catch (error) {
      console.error("Error importing data:", error)
      if (error instanceof ApiError) {
        const apiError = error as TracecatApiError
        form.setError("root", {
          message:
            typeof apiError.body.detail === "object"
              ? JSON.stringify(apiError.body.detail)
              : String(apiError.body.detail),
        })
      } else {
        toast({
          title: "Failed to import data",
          description: "Please try again",
        })
      }
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="max-w-3xl"
        aria-describedby="csv-import-description"
      >
        <DialogHeader className="space-y-4">
          <DialogTitle>Import from CSV</DialogTitle>
          <DialogDescription>
            Import data from a CSV file into your table.
          </DialogDescription>
        </DialogHeader>

        <FormProvider {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-8">
            <div className="space-y-6">
              {!csvPreview ? (
                <CsvUploadForm
                  isUploading={isUploading}
                  nextPage={handleCreatePreview}
                />
              ) : (
                <>
                  <CsvPreview csvData={csvPreview} />
                  {table && (
                    <ColumnMapping csvData={csvPreview} table={table} />
                  )}
                  <div>
                    {form.formState.errors.root && (
                      <div className="text-sm text-red-500">
                        {form.formState.errors.root.message}
                      </div>
                    )}
                  </div>
                  <div className="flex justify-end space-x-2">
                    <Button
                      variant="outline"
                      onClick={() => {
                        setCsvPreview(null)
                        form.reset({ columnMapping: {} })
                      }}
                    >
                      Back
                    </Button>
                    <Button
                      type="submit"
                      disabled={
                        importCsvIsPending ||
                        Object.keys(form.getValues("columnMapping")).length ===
                          0
                      }
                    >
                      {importCsvIsPending ? (
                        <>
                          <Spinner className="mr-2 size-4" />
                          Importing...
                        </>
                      ) : (
                        "Import Data"
                      )}
                    </Button>
                  </div>
                </>
              )}
            </div>
          </form>
        </FormProvider>
      </DialogContent>
    </Dialog>
  )
}

interface CsvUploadFormProps {
  isUploading: boolean
  nextPage: () => void
}

function CsvUploadForm({ isUploading, nextPage }: CsvUploadFormProps) {
  const form = useFormContext<CsvImportFormValues>()
  return (
    <div className="space-y-4">
      <FormField
        control={form.control}
        name="file"
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
              Upload file (max {FILE_SIZE_LIMIT_MB}MB)
            </FormDescription>
            <FormMessage />
          </FormItem>
        )}
      />
      <Button
        onClick={async () => {
          const isValid = await form.trigger("file")
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

interface CsvPreviewProps {
  csvData: CsvPreviewData
}

function CsvPreview({ csvData }: CsvPreviewProps) {
  return (
    <div className="space-y-4">
      <div className="text-sm font-medium">Preview (first 5 rows)</div>
      <div className="max-h-60 overflow-auto rounded border">
        <Table className="min-w-full table-fixed">
          <TableHeader>
            <TableRow>
              {csvData.headers.map((header) => {
                return (
                  <TableHead
                    key={header}
                    className="sticky top-0 min-w-[160px] whitespace-nowrap bg-muted/50"
                  >
                    {header}
                  </TableHead>
                )
              })}
            </TableRow>
          </TableHeader>
          <TableBody>
            {csvData.preview.map((row, i) => {
              return (
                <TableRow key={i}>
                  {csvData.headers.map((header) => {
                    const cellValue = row[header]
                    const isObject =
                      typeof cellValue === "object" && cellValue !== null
                    let displayValue
                    if (isObject) {
                      const jsonString = JSON.stringify(cellValue)
                      if (jsonString.length > 30) {
                        displayValue = jsonString.substring(0, 27) + "..."
                      } else {
                        displayValue = jsonString
                      }
                    } else {
                      displayValue = String(cellValue || "")
                    }

                    return (
                      <TableCell
                        key={header}
                        className="min-w-[160px] truncate"
                        title={
                          isObject
                            ? JSON.stringify(cellValue)
                            : String(cellValue || "")
                        }
                      >
                        {displayValue}
                      </TableCell>
                    )
                  })}
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}

interface ColumnMappingProps {
  csvData: CsvPreviewData
  table: TableRead
}

function ColumnMapping({ csvData, table }: ColumnMappingProps) {
  const form = useFormContext<CsvImportFormValues>()

  return (
    <div className="space-y-4">
      <div className="text-sm font-medium">
        Map CSV columns to table columns
      </div>
      <FormField
        control={form.control}
        name="columnMapping"
        render={() => (
          <FormItem>
            <div className="space-y-2">
              {csvData.headers.map((header) => (
                <div key={header} className="flex items-center gap-2">
                  <FormLabel className="w-1/3 text-sm">{header}</FormLabel>
                  <Select
                    value={form.watch(`columnMapping.${header}`)}
                    onValueChange={(value) =>
                      form.setValue(`columnMapping.${header}`, value)
                    }
                  >
                    <SelectTrigger className="w-2/3">
                      <SelectValue placeholder="Select a column" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="skip">Skip this column</SelectItem>
                      {table.columns.map((column) => (
                        <SelectItem key={column.name} value={column.name}>
                          <div className="flex w-full items-center justify-between gap-2">
                            <span className="text-xs font-medium">
                              {column.name}
                            </span>
                            <SqlTypeDisplay
                              type={column.type as SqlType}
                              className="gap-1.5 text-muted-foreground"
                              iconClassName="size-3"
                              labelClassName="text-xs font-normal"
                            />
                          </div>
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              ))}
            </div>
            <FormMessage />
          </FormItem>
        )}
      />
    </div>
  )
}
