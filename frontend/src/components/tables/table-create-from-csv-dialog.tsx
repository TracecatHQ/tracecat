"use client"

import { useCallback, useEffect, useState } from "react"
import { useWorkspace } from "@/providers/workspace"
import { zodResolver } from "@hookform/resolvers/zod"
import { FormProvider, useForm, useFormContext } from "react-hook-form"
import { z } from "zod"

import { useCreateTableFromCsv, useInferColumnsFromFile } from "@/lib/hooks"
import { CsvPreviewData, getCsvPreview, SqlTypeEnum } from "@/lib/tables"
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
import { toast } from "@/components/ui/use-toast"
// Import shared CSV components
import {
  csvFileSchema,
  CsvPreview,
  CsvUploadForm,
} from "@/components/tables/csv-components"

// Form schema for CSV import
const csvCreateTableSchema = z.object({
  file: csvFileSchema, // Use shared schema validation
  tableName: z.string().min(1, "Table name is required"),
  columnTypes: z.record(z.string(), z.enum(SqlTypeEnum)),
})

type CsvCreateTableFormValues = z.infer<typeof csvCreateTableSchema>

interface TableCreateFromCsvDialogProps {
  open?: boolean
  onOpenChange?: (open: boolean) => void
  onTableCreated?: () => void
}

export function TableCreateFromCsvDialog({
  open,
  onOpenChange,
  onTableCreated,
}: TableCreateFromCsvDialogProps) {
  const { workspaceId } = useWorkspace()
  const { inferColumns } = useInferColumnsFromFile()

  const [currentStep, setCurrentStep] = useState<"upload" | "preview">("upload")
  const [isUploading, setIsUploading] = useState(false)
  const [csvPreview, setCsvPreview] = useState<CsvPreviewData | null>(null)
  const [inferredColumns, setInferredColumns] =
    useState<Array<{ name: string; type: string; sample_values?: unknown[] }>>()
  const { createTableFromCsv } = useCreateTableFromCsv()
  const form = useForm<CsvCreateTableFormValues>({
    resolver: zodResolver(csvCreateTableSchema),
    defaultValues: {
      tableName: "",
      columnTypes: {},
    },
  })

  useEffect(() => {
    if (!open) {
      setCsvPreview(null)
      setInferredColumns([])
      setCurrentStep("upload")
      form.reset()
    }
  }, [open, form])

  const handleCreatePreview = useCallback(async () => {
    const { file } = form.getValues()
    try {
      setIsUploading(true)
      const parsedData = await getCsvPreview(file)
      setCsvPreview(parsedData)

      // Send the entire file for inference instead of just the sample data
      const formData = new FormData()
      formData.append("file", file)

      // Call the infer-types-from-file endpoint
      const inferred = await inferColumns({
        formData, // Fixed: Use the formData object instead of file_content
        workspaceId,
      })

      setInferredColumns(inferred)

      // Set default column types based on inference
      const columnTypes = {} as Record<string, (typeof SqlTypeEnum)[number]>
      inferred.forEach(
        (col: { name: string; type: string; sample_values?: unknown[] }) => {
          columnTypes[col.name] = col.type as (typeof SqlTypeEnum)[number]
        }
      )

      form.setValue("columnTypes", columnTypes)

      setCurrentStep("preview")
    } catch (error) {
      console.error("Error parsing CSV preview:", error)
      toast({
        title: "Error",
        description: "Failed to parse CSV file",
      })
    } finally {
      setIsUploading(false)
    }
  }, [form, inferColumns, workspaceId])

  const handleCreateTable = async (data: CsvCreateTableFormValues) => {
    try {
      if (!csvPreview) {
        toast({
          title: "Error",
          description: "No CSV data available",
          variant: "destructive",
        })
        return
      }

      const columns = csvPreview.headers.map((header) => ({
        name: header,
        type: data.columnTypes[header] as (typeof SqlTypeEnum)[number],
      }))

      await createTableFromCsv({
        tableName: data.tableName,
        columns: columns,
        file: data.file,
        workspaceId: workspaceId,
      })

      onOpenChange?.(false)
      onTableCreated?.()
    } catch (error) {
      console.error("Error creating table:", error)
      toast({
        title: "Error",
        description:
          error instanceof Error ? error.message : "Failed to create table",
        variant: "destructive",
      })
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogHeader className="space-y-4">
          <DialogTitle>Create table from CSV</DialogTitle>
          <DialogDescription>
            Import a CSV file to create a new table with the detected schema.
          </DialogDescription>
        </DialogHeader>

        <FormProvider {...form}>
          <form
            onSubmit={form.handleSubmit(handleCreateTable)}
            className="space-y-8"
          >
            <div className="space-y-6">
              {currentStep === "upload" ? (
                <CsvUploadForm
                  isUploading={isUploading}
                  nextPage={handleCreatePreview}
                />
              ) : (
                <>
                  <FormField
                    control={form.control}
                    name="tableName"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Table Name</FormLabel>
                        <FormControl>
                          <Input placeholder="Enter table name..." {...field} />
                        </FormControl>
                        <FormDescription>
                          Name for the new table
                        </FormDescription>
                        <FormMessage />
                      </FormItem>
                    )}
                  />

                  {csvPreview && <CsvPreview csvData={csvPreview} />}

                  {csvPreview && (
                    <ColumnTypeMapping
                      csvHeaders={csvPreview.headers}
                      inferredColumns={inferredColumns || []}
                    />
                  )}

                  <div className="flex justify-end space-x-2">
                    <Button
                      variant="outline"
                      onClick={() => {
                        setCurrentStep("upload")
                      }}
                    >
                      Back
                    </Button>
                    <Button type="submit">Create Table</Button>
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

interface ColumnTypeMappingProps {
  csvHeaders: string[]
  inferredColumns: Array<{
    name: string
    type: string
    sample_values?: unknown[]
  }>
}

function ColumnTypeMapping({
  csvHeaders,
  inferredColumns,
}: ColumnTypeMappingProps) {
  const form = useFormContext<CsvCreateTableFormValues>()

  // Create a mapping of header names to inferred column types and sample values
  const inferredTypes: Record<string, string> = {}
  const sampleValuesMap: Record<string, unknown[]> = {}

  inferredColumns.forEach((col) => {
    inferredTypes[col.name] = col.type
    if (col.sample_values) {
      sampleValuesMap[col.name] = col.sample_values
    }
  })

  return (
    <div className="space-y-4">
      <div className="text-sm font-medium">Column Types</div>
      <div className="space-y-4">
        {csvHeaders.map((header) => (
          <div key={header} className="space-y-2">
            <div className="flex items-center gap-2">
              <FormLabel className="w-1/3 text-sm">{header}</FormLabel>
              <FormField
                control={form.control}
                name={`columnTypes.${header}`}
                render={({ field }) => (
                  <Select
                    value={field.value}
                    onValueChange={field.onChange}
                    defaultValue={inferredTypes[header] || SqlTypeEnum[0]}
                  >
                    <SelectTrigger className="w-2/3">
                      <SelectValue placeholder="Select type" />
                    </SelectTrigger>
                    <SelectContent>
                      {SqlTypeEnum.map((type) => (
                        <SelectItem key={type} value={type}>
                          {type}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
              />
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
