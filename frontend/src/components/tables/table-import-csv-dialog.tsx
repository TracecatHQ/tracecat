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
import { ProtectedColumnsAlert } from "@/components/tables/protected-columns-alert"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
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
        const detail =
          typeof apiError.body.detail === "string"
            ? apiError.body.detail
            : typeof apiError.body.detail === "object"
              ? JSON.stringify(apiError.body.detail)
              : undefined
        let message: string
        if (
          detail?.toLowerCase().includes("column") &&
          detail?.includes("already exists")
        ) {
          message =
            "A column in your CSV conflicts with an existing column. Check for protected names like id, created_at, or updated_at."
        } else if (detail && error.status !== 500) {
          message = detail
        } else {
          message =
            "Failed to import data. Please check your CSV file and try again."
        }
        form.setError("root", { type: "manual", message })
      } else {
        form.setError("root", {
          type: "manual",
          message: "Failed to import data. Please try again.",
        })
      }
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="flex max-h-[85vh] max-w-3xl flex-col overflow-hidden"
        aria-describedby="csv-import-description"
      >
        <DialogHeader className="space-y-4">
          <DialogTitle>Import from CSV</DialogTitle>
          <DialogDescription>
            Import data from a CSV file into your table.
          </DialogDescription>
          <ProtectedColumnsAlert />
        </DialogHeader>

        <FormProvider {...form}>
          <form
            onSubmit={form.handleSubmit(onSubmit)}
            className="flex min-h-0 flex-1 flex-col overflow-hidden"
          >
            <div className="no-scrollbar min-h-0 flex-1 overflow-y-auto">
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
                  </>
                )}
              </div>
            </div>

            {csvPreview && (
              <DialogFooter className="pt-4">
                {form.formState.errors.root && (
                  <div className="mr-auto text-sm text-red-500">
                    {form.formState.errors.root.message}
                  </div>
                )}
                <Button
                  type="button"
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
                    Object.keys(form.watch("columnMapping")).length === 0
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
              </DialogFooter>
            )}
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
                className="min-h-9 py-2"
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

export function CsvPreview({ csvData }: CsvPreviewProps) {
  return (
    <div className="min-w-0 space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium">Preview</span>
        <span className="text-xs text-muted-foreground">
          {csvData.headers.length} columns &middot; {csvData.preview.length}{" "}
          rows
        </span>
      </div>
      <div className="no-scrollbar max-h-[240px] overflow-auto rounded-md border border-border/50">
        <table className="w-full min-w-max table-auto border-separate border-spacing-0 text-xs">
          <thead>
            <tr>
              <th className="sticky left-0 top-0 z-20 min-w-[40px] border-b border-r border-border/30 bg-muted/30 px-3 py-2 text-left font-medium text-muted-foreground">
                #
              </th>
              {csvData.headers.map((header) => (
                <th
                  key={header}
                  className="sticky top-0 z-10 min-w-[120px] max-w-[200px] whitespace-nowrap border-b border-border/30 bg-muted/30 px-3 py-2 text-left font-medium"
                >
                  <div className="truncate" title={header}>
                    {header}
                  </div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {csvData.preview.map((row, i) => (
              <tr key={i}>
                <td className="sticky left-0 z-10 border-r border-border/30 bg-background px-3 py-1.5 font-mono text-[10px] text-muted-foreground">
                  {i + 1}
                </td>
                {csvData.headers.map((header) => {
                  const cellValue = row[header]
                  const isObject =
                    typeof cellValue === "object" && cellValue !== null
                  const fullValue = isObject
                    ? JSON.stringify(cellValue)
                    : String(cellValue ?? "")
                  const displayValue =
                    fullValue.length > 80
                      ? `${fullValue.substring(0, 77)}...`
                      : fullValue

                  return (
                    <td
                      key={header}
                      className="min-w-[120px] max-w-[200px] truncate px-3 py-1.5"
                      title={fullValue}
                    >
                      {displayValue}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
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
