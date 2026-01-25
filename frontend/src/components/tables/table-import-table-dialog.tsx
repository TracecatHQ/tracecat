"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import type { DialogProps } from "@radix-ui/react-dialog"
import { useEffect, useState } from "react"
import { FormProvider, useForm, useFormContext } from "react-hook-form"
import { z } from "zod"
import { ApiError } from "@/client"
import { Spinner } from "@/components/loading/spinner"
import { CsvPreview } from "@/components/tables/table-import-csv-dialog"
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
import type { TracecatApiError } from "@/lib/errors"
import { useImportTableFromCsv } from "@/lib/hooks"
import { type CsvPreviewData, getCsvPreview } from "@/lib/tables"
import { useWorkspaceId } from "@/providers/workspace-id"

const BYTES_PER_MB = 1024 * 1024
const FILE_SIZE_LIMIT_MB = 5

const tableImportSchema = z.object({
  file: z.instanceof(File).refine(
    (file) => {
      if (file.size > FILE_SIZE_LIMIT_MB * BYTES_PER_MB) {
        return false
      }
      const lowerName = file.name?.toLowerCase() ?? ""
      return file.type === "text/csv" || lowerName.endsWith(".csv")
    },
    (file) => ({
      message: `Please upload a CSV file under 5MB. Current file size: ${(file.size / BYTES_PER_MB).toFixed(2)}MB`,
    })
  ),
  tableName: z
    .string()
    .trim()
    .max(100, "Name cannot exceed 100 characters")
    .regex(
      /^[a-zA-Z_][a-zA-Z0-9_]*$/,
      "Name must contain only letters, numbers, and underscores, and start with a letter or underscore"
    )
    .optional()
    .or(z.literal(""))
    .transform((value) => {
      if (!value) return undefined
      const trimmed = value.trim()
      return trimmed === "" ? undefined : trimmed
    }),
})

type TableImportFormValues = z.infer<typeof tableImportSchema>

interface TableImportTableDialogProps extends DialogProps {
  open?: boolean
  onOpenChange?: (open: boolean) => void
}

export function TableImportTableDialog({
  open,
  onOpenChange,
}: TableImportTableDialogProps) {
  const workspaceId = useWorkspaceId()
  const { importTable, importTableIsPending } = useImportTableFromCsv()
  const [csvPreview, setCsvPreview] = useState<CsvPreviewData | null>(null)
  const [isParsing, setIsParsing] = useState(false)
  const form = useForm<TableImportFormValues>({
    resolver: zodResolver(tableImportSchema),
    defaultValues: {
      tableName: "",
    },
  })

  useEffect(() => {
    if (!open) {
      setCsvPreview(null)
      form.reset()
    }
  }, [open, form])

  const createPreview = async (file: File) => {
    try {
      setIsParsing(true)
      setCsvPreview(null)
      const parsed = await getCsvPreview(file)
      setCsvPreview(parsed)
    } catch (error) {
      console.error("Failed to parse CSV preview:", error)
      form.setError("file", {
        type: "manual",
        message: "Unable to read CSV file. Please check the file format.",
      })
    } finally {
      setIsParsing(false)
    }
  }

  const handleSubmit = async (values: TableImportFormValues) => {
    try {
      const file = values.file
      if (!file) {
        form.setError("file", {
          type: "manual",
          message: "Please select a CSV file to import.",
        })
        return
      }

      await importTable({
        workspaceId,
        formData: {
          file,
          table_name: values.tableName ?? null,
        },
      })

      onOpenChange?.(false)
      form.reset()
      setCsvPreview(null)
    } catch (error) {
      if (error instanceof ApiError) {
        const apiError = error as TracecatApiError
        const detail =
          typeof apiError.body.detail === "string"
            ? apiError.body.detail
            : typeof apiError.body.detail === "object"
              ? JSON.stringify(apiError.body.detail)
              : undefined
        form.setError("file", {
          type: "manual",
          message:
            detail && detail !== "{}" ? detail : "Failed to import table",
        })
      } else {
        console.error("Unexpected error importing table:", error)
        form.setError("file", {
          type: "manual",
          message: "Failed to import table",
        })
      }
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>Import table from CSV</DialogTitle>
          <DialogDescription>
            Upload a CSV file to create a new table. Columns and data types will
            be inferred automatically.
          </DialogDescription>
        </DialogHeader>
        <FormProvider {...form}>
          <form
            onSubmit={form.handleSubmit(handleSubmit)}
            className="space-y-6"
          >
            <FormField
              control={form.control}
              name="file"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>CSV file</FormLabel>
                  <FormControl>
                    <Input
                      type="file"
                      accept=".csv,text/csv"
                      className="min-h-9 w-full py-2"
                      onChange={async (event) => {
                        const file = event.target.files?.[0]
                        if (!file) {
                          field.onChange(undefined)
                          setCsvPreview(null)
                          return
                        }
                        field.onChange(file)
                        await createPreview(file)
                      }}
                      name={field.name}
                      ref={field.ref}
                      onBlur={field.onBlur}
                    />
                  </FormControl>
                  <FormDescription>
                    CSV files up to {FILE_SIZE_LIMIT_MB}MB are supported.
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />

            {csvPreview && (
              <div className="space-y-2">
                <CsvPreview csvData={csvPreview} />
                <p className="text-xs text-muted-foreground">
                  Column names will be sanitised automatically. Duplicate or
                  invalid column names are adjusted to remain unique.
                </p>
              </div>
            )}

            <TableNameField />

            <DialogFooter className="gap-2 sm:justify-between">
              <div className="text-xs text-muted-foreground">
                Leave the table name blank to derive it from the file name.
              </div>
              <div className="flex gap-2">
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => onOpenChange?.(false)}
                  disabled={importTableIsPending}
                >
                  Cancel
                </Button>
                <Button
                  type="submit"
                  disabled={importTableIsPending || isParsing}
                >
                  {importTableIsPending || isParsing ? (
                    <>
                      <Spinner className="mr-2" />
                      Importing...
                    </>
                  ) : (
                    "Import table"
                  )}
                </Button>
              </div>
            </DialogFooter>
          </form>
        </FormProvider>
      </DialogContent>
    </Dialog>
  )
}

function TableNameField() {
  const form = useFormContext<TableImportFormValues>()

  return (
    <FormField
      control={form.control}
      name="tableName"
      render={({ field }) => (
        <FormItem>
          <FormLabel>Table name (optional)</FormLabel>
          <FormControl>
            <Input
              placeholder="Enter table name or leave blank"
              {...field}
              value={field.value ?? ""}
            />
          </FormControl>
          <FormMessage />
        </FormItem>
      )}
    />
  )
}
