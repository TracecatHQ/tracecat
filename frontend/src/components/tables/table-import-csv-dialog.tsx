"use client"

import { useCallback, useEffect, useState } from "react"
import { useParams } from "next/navigation"
import { ApiError, TableRead } from "@/client"
import { useWorkspace } from "@/providers/workspace"
import { zodResolver } from "@hookform/resolvers/zod"
import { DialogProps } from "@radix-ui/react-dialog"
import { FormProvider, useForm, useFormContext } from "react-hook-form"
import { z } from "zod"

import { TracecatApiError } from "@/lib/errors"
import { useGetTable, useImportCsv } from "@/lib/hooks"
import { CsvPreviewData, getCsvPreview } from "@/lib/tables"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { toast } from "@/components/ui/use-toast"
import { Spinner } from "@/components/loading/spinner"
// Import shared CSV components
import {
  csvFileSchema,
  CsvPreview,
  CsvUploadForm,
} from "@/components/tables/csv-components"

// Form schema for Csv import
const csvImportSchema = z.object({
  file: csvFileSchema, // Use shared schema validation
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
  const { tableId } = useParams<{ tableId: string }>()
  const { workspaceId } = useWorkspace()
  const { table } = useGetTable({ tableId, workspaceId })
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
        tableId,
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
                          <span>{column.name}</span>
                          <span className="ml-3 lowercase text-muted-foreground">
                            {column.type}
                          </span>
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
