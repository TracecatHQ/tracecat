// frontend/src/components/tables/TableCreateFromCsvDialog.tsx
"use client"

import { useCallback, useEffect, useState } from "react"
import { useWorkspace } from "@/providers/workspace"
import { zodResolver } from "@hookform/resolvers/zod"
import { FormProvider, useForm, useFormContext } from "react-hook-form"
import { z } from "zod"
import { useCreateTableFromCsv } from "@/lib/hooks"

import { useInferColumnsFromCSV } from "@/lib/hooks"
import { CsvPreviewData, SqlTypeEnum, getCsvPreview } from "@/lib/tables"
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
import { Spinner } from "@/components/loading/spinner"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { tablesCreateTableFromCsv } from "@/client"

const BYTES_PER_MB = 1024 * 1024
const FILE_SIZE_LIMIT_MB = 5

// Form schema for CSV import
const csvCreateTableSchema = z.object({
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
  tableName: z.string().min(1, "Table name is required"),
  columnTypes: z.record(z.string(), z.enum(SqlTypeEnum)),
})

type CsvCreateTableFormValues = z.infer<typeof csvCreateTableSchema>

interface TableCreateFromCsvDialogProps {
    open?: boolean
    onOpenChange?: (open: boolean) => void
    onTableCreated?: () => void  // Add this callback prop
}

export function TableCreateFromCsvDialog({
  open,
  onOpenChange,
  onTableCreated,
}: TableCreateFromCsvDialogProps) {
  const { workspaceId } = useWorkspace()
  const { inferColumns, inferColumnsPending } = useInferColumnsFromCSV()

  const [currentStep, setCurrentStep] = useState<'upload' | 'preview'>('upload')
  const [isUploading, setIsUploading] = useState(false)
  const [csvPreview, setCsvPreview] = useState<CsvPreviewData | null>(null)
  const [inferredColumns, setInferredColumns] = useState<Array<{name: string; type: string; sample_value?: unknown}>>([])
  const { createTableFromCsv, createTableFromCsvIsPending, createTableFromCsvError } = useCreateTableFromCsv()
  const form = useForm<CsvCreateTableFormValues>({
    resolver: zodResolver(csvCreateTableSchema),
    defaultValues: {
      tableName: '',
      columnTypes: {},
    },
  })

  // Reset state when dialog closes
  useEffect(() => {
    if (!open) {
      setCsvPreview(null)
      setInferredColumns([])
      setCurrentStep('upload')
      form.reset()
    }
  }, [open, form])

  const handleCreatePreview = useCallback(async () => {
    const { file } = form.getValues()
    try {
      setIsUploading(true)
      const parsedData = await getCsvPreview(file)
      setCsvPreview(parsedData)

      // Create a sample row from the first row of data
      if (parsedData.preview.length > 0) {
        const sampleRow = parsedData.preview[0]

        // Convert string values to appropriate types before sending
        const typedSampleRow = Object.fromEntries(
          Object.entries(sampleRow).map(([key, value]) => {
            // Skip null/undefined/empty values
            if (value === null || value === undefined || value === "") {
              return [key, null];
            }

            // Try to parse JSON
            if (typeof value === 'string' && (value.trim().startsWith('{') || value.trim().startsWith('['))) {
              try {
                return [key, JSON.parse(value)];
              } catch (e) {
                // If JSON parsing fails, continue with other type conversions
                console.log(`Failed to parse JSON for ${key}:`, e);
              }
            }

            // Try to convert to number
            const num = Number(value);
            if (!isNaN(num)) {
              return [key, num];
            }

            // Check for boolean
            if (value.toLowerCase() === "true") return [key, true];
            if (value.toLowerCase() === "false") return [key, false];

            // Default to string
            return [key, value];
          })
        );

        // Infer column types with the converted values
        const inferred = await inferColumns({
          requestBody: typedSampleRow,
          workspaceId
        })
        setInferredColumns(inferred)

        // Set default column types based on inference
        const columnTypes = {} as Record<string, typeof SqlTypeEnum[number]>
        inferred.forEach((col: {name: string; type: string; sample_value?: unknown}) => {
            columnTypes[col.name] = col.type as typeof SqlTypeEnum[number]
        })
        form.setValue('columnTypes', columnTypes)
      }

      setCurrentStep('preview')
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
          variant: "destructive"
        })
        return
      }

      const columns = csvPreview.headers.map(header => ({
        name: header,
        type: data.columnTypes[header] as typeof SqlTypeEnum[number]
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
        description: error instanceof Error ? error.message : "Failed to create table",
        variant: "destructive"
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
          <form onSubmit={form.handleSubmit(handleCreateTable)} className="space-y-8">
            <div className="space-y-6">
              {currentStep === 'upload' ? (
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
                          <Input
                            placeholder="Enter table name..."
                            {...field}
                          />
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
                      inferredColumns={inferredColumns}
                    />
                  )}

                  <div className="flex justify-end space-x-2">
                    <Button
                      variant="outline"
                      onClick={() => {
                        setCurrentStep('upload')
                      }}
                    >
                      Back
                    </Button>
                    <Button type="submit">
                      Create Table
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
  const form = useFormContext<CsvCreateTableFormValues>()
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
                {csvData.headers.map((header) => {
                  const cellValue = row[header];
                  const isObject = typeof cellValue === 'object' && cellValue !== null;
                  const displayValue = isObject
                    ? JSON.stringify(cellValue).length > 30
                      ? JSON.stringify(cellValue).substring(0, 27) + "..."
                      : JSON.stringify(cellValue)
                    : String(cellValue || '');

                  return (
                    <TableCell
                      key={header}
                      className="min-w-[160px] truncate"
                      title={isObject ? JSON.stringify(cellValue) : String(cellValue || '')}
                    >
                      {displayValue}
                    </TableCell>
                  )})}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}

interface ColumnTypeMappingProps {
    csvHeaders: string[]
    inferredColumns: Array<{name: string; type: string; sample_value?: unknown}>
}

function ColumnTypeMapping({ csvHeaders, inferredColumns }: ColumnTypeMappingProps) {
  const form = useFormContext<CsvCreateTableFormValues>()

  // Create a mapping of header names to inferred column types
  const inferredTypes: Record<string, string> = {}
  inferredColumns.forEach(col => {
    inferredTypes[col.name] = col.type
  })

  return (
    <div className="space-y-4">
      <div className="text-sm font-medium">
        Column Types
      </div>
      <div className="space-y-2">
        {csvHeaders.map((header) => (
          <div key={header} className="flex items-center gap-2">
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
        ))}
      </div>
    </div>
  )
}
