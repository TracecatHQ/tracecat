"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import type { DialogProps } from "@radix-ui/react-dialog"
import { useCallback, useEffect, useState } from "react"
import { FormProvider, useForm, useFormContext } from "react-hook-form"
import { z } from "zod"
import { ApiError } from "@/client"
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
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { toast } from "@/components/ui/use-toast"
import { useCreateTableFromCsv } from "@/lib/hooks"
import { type CsvPreviewData, getCsvPreview } from "@/lib/tables"
import { useWorkspaceId } from "@/providers/workspace-id"

const BYTES_PER_MB = 1024 * 1024
const FILE_SIZE_LIMIT_MB = 5

const createTableFromCsvSchema = z.object({
  name: z
    .string()
    .min(1, "Name is required")
    .max(100, "Name cannot exceed 100 characters")
    .regex(
      /^[a-zA-Z0-9_]+$/,
      "Name must contain only letters, numbers, and underscores"
    ),
  file: z.instanceof(File).refine(
    (file) => {
      if (file.size > FILE_SIZE_LIMIT_MB * BYTES_PER_MB) {
        return false
      }
      return file.type === "text/csv" || file.name.endsWith(".csv")
    },
    (file) => ({
      message: `Please upload a CSV file under 5MB. Current file size: ${(file.size / BYTES_PER_MB).toFixed(2)}MB`,
    })
  ),
})

type CreateTableFromCsvFormValues = z.infer<typeof createTableFromCsvSchema>

interface TableCreateFromCsvDialogProps extends DialogProps {
  open?: boolean
  onOpenChange?: (open: boolean) => void
}

export function TableCreateFromCsvDialog({
  open,
  onOpenChange,
}: TableCreateFromCsvDialogProps) {
  const workspaceId = useWorkspaceId()
  const { createTableFromCsv, createTableFromCsvIsPending } =
    useCreateTableFromCsv()

  const [isUploading, setIsUploading] = useState(false)
  const [csvPreview, setCsvPreview] = useState<CsvPreviewData | null>(null)

  const form = useForm<CreateTableFromCsvFormValues>({
    resolver: zodResolver(createTableFromCsvSchema),
    defaultValues: {
      name: "",
    },
  })

  useEffect(() => {
    if (!open) {
      setCsvPreview(null)
      form.reset({ name: "" })
    }
  }, [open, form])

  const handleGeneratePreview = useCallback(async () => {
    const { file } = form.getValues()
    if (!file) return

    try {
      setIsUploading(true)
      const preview = await getCsvPreview(file)
      setCsvPreview(preview)
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

  const onSubmit = async ({ name, file }: CreateTableFromCsvFormValues) => {
    if (!workspaceId) {
      toast({
        title: "Workspace unavailable",
        description: "Please select a workspace before importing.",
      })
      return
    }

    try {
      await createTableFromCsv({
        workspaceId,
        formData: { name, file },
      })
      onOpenChange?.(false)
      setCsvPreview(null)
      form.reset({ name: "" })
    } catch (error) {
      if (error instanceof ApiError) {
        if (error.status === 409) {
          form.setError("name", {
            type: "manual",
            message: "A table with this name already exists",
          })
        } else {
          form.setError("root", {
            type: "manual",
            message:
              typeof error.body === "object" && error.body !== null
                ? JSON.stringify(error.body)
                : "Failed to import CSV. Please try again.",
          })
        }
      } else {
        form.setError("root", {
          type: "manual",
          message: "An unexpected error occurred",
        })
        console.error("Error creating table from CSV:", error)
      }
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="max-w-3xl"
        aria-describedby="create-table-from-csv-description"
      >
        <DialogHeader className="space-y-4">
          <DialogTitle>Create table from CSV</DialogTitle>
          <DialogDescription id="create-table-from-csv-description">
            Import a CSV file to create a new table with inferred columns.
          </DialogDescription>
        </DialogHeader>

        <FormProvider {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-8">
            <div className="space-y-6">
              {!csvPreview ? (
                <CsvSetupForm
                  isUploading={isUploading}
                  onNext={async () => {
                    const valid = await form.trigger(["name", "file"])
                    if (!valid) return
                    await handleGeneratePreview()
                  }}
                />
              ) : (
                <>
                  <CsvPreview csvData={csvPreview} />
                  {form.formState.errors.root && (
                    <div className="text-sm text-red-500">
                      {form.formState.errors.root.message}
                    </div>
                  )}
                  <div className="flex justify-end space-x-2">
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => setCsvPreview(null)}
                      disabled={createTableFromCsvIsPending}
                    >
                      Back
                    </Button>
                    <Button
                      type="submit"
                      disabled={createTableFromCsvIsPending}
                    >
                      {createTableFromCsvIsPending ? (
                        <>
                          <Spinner className="mr-2 size-4" />
                          Importing...
                        </>
                      ) : (
                        "Create table"
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

interface CsvSetupFormProps {
  isUploading: boolean
  onNext: () => void
}

function CsvSetupForm({ isUploading, onNext }: CsvSetupFormProps) {
  const form = useFormContext<CreateTableFromCsvFormValues>()
  return (
    <div className="space-y-6">
      <FormField
        control={form.control}
        name="name"
        render={({ field }) => (
          <FormItem>
            <FormLabel>Table name</FormLabel>
            <FormControl>
              <Input
                placeholder="Enter table name..."
                {...field}
                value={field.value ?? ""}
              />
            </FormControl>
            <FormDescription>
              Name must be unique and contain only letters, numbers, or
              underscores.
            </FormDescription>
            <FormMessage />
          </FormItem>
        )}
      />
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
                onChange={(event) => {
                  const file = event.target.files?.[0]
                  if (file) {
                    field.onChange(file)
                  }
                }}
              />
            </FormControl>
            <FormDescription>
              Upload a CSV file (max {FILE_SIZE_LIMIT_MB}MB). Columns will be
              inferred automatically.
            </FormDescription>
            <FormMessage />
          </FormItem>
        )}
      />
      <div className="flex justify-end">
        <Button onClick={onNext} disabled={isUploading}>
          {isUploading ? (
            <>
              <Spinner className="mr-2 size-4" />
              Processing...
            </>
          ) : (
            "Next"
          )}
        </Button>
      </div>
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
            {csvData.preview.map((row, rowIndex) => (
              <TableRow key={rowIndex}>
                {csvData.headers.map((header) => {
                  const cellValue = row[header]
                  const isObject =
                    typeof cellValue === "object" && cellValue !== null
                  let displayValue
                  if (isObject) {
                    const jsonString = JSON.stringify(cellValue)
                    displayValue =
                      jsonString.length > 30
                        ? `${jsonString.substring(0, 27)}...`
                        : jsonString
                  } else {
                    displayValue = String(cellValue ?? "")
                  }

                  return (
                    <TableCell
                      key={header}
                      className="min-w-[160px] truncate"
                      title={
                        isObject
                          ? JSON.stringify(cellValue)
                          : String(cellValue ?? "")
                      }
                    >
                      {displayValue}
                    </TableCell>
                  )
                })}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}
