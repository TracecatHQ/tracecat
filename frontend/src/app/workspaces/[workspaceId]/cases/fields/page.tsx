"use client"

import { useState } from "react"
import {
  CaseFieldCreate,
  CaseFieldRead,
  casesCreateField,
  casesDeleteField,
} from "@/client"
import { useWorkspace } from "@/providers/workspace"
import { zodResolver } from "@hookform/resolvers/zod"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { DatabaseIcon, TrashIcon } from "lucide-react"
import { useForm } from "react-hook-form"
import { z } from "zod"

import { useCaseFields } from "@/lib/hooks"
import { SqlTypeEnum } from "@/lib/tables"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Form,
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
import { Separator } from "@/components/ui/separator"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { toast } from "@/components/ui/use-toast"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"

const caseFieldFormSchema = z.object({
  name: z
    .string()
    .min(1, "Field name is required")
    .max(100, "Field name must be less than 100 characters")
    .refine(
      (value) => /^[a-zA-Z][a-zA-Z0-9_]*$/.test(value),
      "Field name must start with a letter and contain only letters, numbers, and underscores"
    ),
  type: z.enum(SqlTypeEnum),
  nullable: z.boolean().default(true),
  default: z.string().nullable().optional(),
})

type CaseFieldFormValues = z.infer<typeof caseFieldFormSchema>

export default function CasesFieldsPage() {
  const { workspaceId } = useWorkspace()
  const queryClient = useQueryClient()
  const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false)
  const [fieldToDelete, setFieldToDelete] = useState<string | null>(null)

  const { caseFields, caseFieldsIsLoading, caseFieldsError } =
    useCaseFields(workspaceId)

  const { mutateAsync: createCaseField, isPending: createCaseFieldIsPending } =
    useMutation({
      mutationFn: async (data: CaseFieldCreate) => {
        return await casesCreateField({
          workspaceId,
          requestBody: data,
        })
      },
      onSuccess: () => {
        queryClient.invalidateQueries({
          queryKey: ["case-fields", workspaceId],
        })
        toast({
          title: "Field created",
          description: "The case field was created successfully.",
        })
        form.reset({
          name: "",
          type: "TEXT",
          nullable: true,
          default: null,
        })
      },
      onError: (error) => {
        console.error("Failed to create case field", error)
        toast({
          title: "Error creating field",
          description: "Failed to create the case field. Please try again.",
          variant: "destructive",
        })
      },
    })

  const { mutateAsync: deleteCaseField, isPending: deleteCaseFieldIsPending } =
    useMutation({
      mutationFn: async (fieldId: string) => {
        return await casesDeleteField({
          workspaceId,
          fieldId,
        })
      },
      onSuccess: () => {
        queryClient.invalidateQueries({
          queryKey: ["case-fields", workspaceId],
        })
        toast({
          title: "Field deleted",
          description: "The case field was deleted successfully.",
        })
      },
      onError: (error) => {
        console.error("Failed to delete case field", error)
        toast({
          title: "Error deleting field",
          description: "Failed to delete the case field. Please try again.",
          variant: "destructive",
        })
      },
    })

  const form = useForm<CaseFieldFormValues>({
    resolver: zodResolver(caseFieldFormSchema),
    defaultValues: {
      name: "",
      type: "TEXT",
      nullable: true,
      default: null,
    },
  })

  const onSubmit = async (data: CaseFieldFormValues) => {
    try {
      await createCaseField({
        name: data.name,
        type: data.type,
        nullable: data.nullable,
        default: data.default || null,
      })
    } catch (error) {
      console.error("Failed to create case field", error)
    }
  }

  const handleDeleteField = async (fieldId: string) => {
    // Ensure caseFields exists before attempting to find a field
    if (!caseFields) {
      return
    }

    // Find the field to check if it's reserved
    const field = caseFields.find((f) => f.id === fieldId)

    // Don't allow deletion of reserved fields
    if (field && field.reserved) {
      return
    }

    setFieldToDelete(fieldId)
    setIsDeleteDialogOpen(true)
  }

  const confirmDeleteField = async () => {
    if (fieldToDelete) {
      await deleteCaseField(fieldToDelete)
      setFieldToDelete(null)
    }
    setIsDeleteDialogOpen(false)
  }

  if (caseFieldsIsLoading) {
    return <CenteredSpinner />
  }

  if (caseFieldsError || !caseFields) {
    return (
      <AlertNotification
        level="error"
        message={`Error loading case fields: ${caseFieldsError?.message || "Unknown error"}`}
      />
    )
  }

  return (
    <div className="space-y-8">
      <div className="flex w-full items-center justify-between">
        <div className="items-start space-y-3 text-left">
          <h2 className="text-2xl font-semibold tracking-tight">
            Custom Fields
          </h2>
          <p className="text-md text-muted-foreground">
            Define custom fields to capture additional information on cases.
          </p>
        </div>
      </div>

      <div className="rounded-lg border p-6">
        <div className="mb-4 space-y-3">
          <h3 className="text-lg font-semibold">Existing Fields</h3>
          <p className="text-sm text-muted-foreground">
            These are the custom fields currently available for cases.
          </p>
        </div>
        <div>
          {caseFields.filter((field) => !field.reserved).length === 0 ? (
            <div className="flex h-48 flex-col items-center justify-center gap-4 rounded-md border border-dashed">
              <div className="rounded-full bg-muted p-3">
                <DatabaseIcon className="size-8 text-muted-foreground" />
              </div>
              <div className="space-y-1 text-center">
                <h4 className="text-sm font-semibold text-muted-foreground">
                  No custom fields defined yet
                </h4>
                <p className="text-xs text-muted-foreground">
                  Add your first custom field using the form below
                </p>
              </div>
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Description</TableHead>
                  <TableHead>Nullable</TableHead>
                  <TableHead>Default</TableHead>
                  <TableHead className="w-[50px]"></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {caseFields
                  .filter((field) => !field.reserved)
                  .map((field: CaseFieldRead) => (
                    <TableRow key={field.id}>
                      <TableCell className="font-medium">{field.id}</TableCell>
                      <TableCell>{field.type}</TableCell>
                      <TableCell>{field.description || "-"}</TableCell>
                      <TableCell>{field.nullable ? "Yes" : "No"}</TableCell>
                      <TableCell>{field.default || "-"}</TableCell>
                      <TableCell>
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => handleDeleteField(field.id)}
                          disabled={deleteCaseFieldIsPending}
                        >
                          <TrashIcon className="size-4" />
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
              </TableBody>
            </Table>
          )}
        </div>
      </div>

      <div className="rounded-lg border p-6">
        <div className="mb-4">
          <div>
            <h3 className="text-lg font-semibold">Add New Field</h3>
            <p className="text-sm text-muted-foreground">
              Create a new custom field for cases.
            </p>
          </div>
        </div>
        <div>
          <Form {...form}>
            <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6">
              <FormField
                control={form.control}
                name="name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>field ID</FormLabel>
                    <FormControl>
                      <Input placeholder="e.g., customer_id" {...field} />
                    </FormControl>
                    <FormDescription>
                      A human readable ID of the field. Use snake_case for best
                      compatibility.
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="type"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Field Type</FormLabel>
                    <Select
                      onValueChange={field.onChange}
                      defaultValue={field.value}
                    >
                      <FormControl>
                        <SelectTrigger>
                          <SelectValue placeholder="Select a field type" />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        <SelectItem value="TEXT">TEXT</SelectItem>
                        <SelectItem value="INTEGER">INTEGER</SelectItem>
                        <SelectItem value="DECIMAL">DECIMAL</SelectItem>
                        <SelectItem value="JSONB">JSONB</SelectItem>
                        <SelectItem value="BOOLEAN">BOOLEAN</SelectItem>
                      </SelectContent>
                    </Select>
                    <FormDescription>
                      The SQL data type for this field.
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="nullable"
                render={({ field }) => (
                  <FormItem className="flex flex-row items-start space-x-3 space-y-0">
                    <FormControl>
                      <Checkbox
                        checked={field.value}
                        onCheckedChange={field.onChange}
                        disabled
                      />
                    </FormControl>
                    <div className="space-y-1 leading-none">
                      <FormLabel>Nullable</FormLabel>
                      <FormDescription>
                        Allow this field to have null values.
                      </FormDescription>
                    </div>
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="default"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Default Value (optional)</FormLabel>
                    <FormControl>
                      <Input
                        placeholder="Default value"
                        {...field}
                        value={field.value || ""}
                        onChange={(e) => {
                          const value = e.target.value
                          field.onChange(value === "" ? null : value)
                        }}
                      />
                    </FormControl>
                    <FormDescription>
                      The default value for this field if not specified.
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <Separator />

              <Button
                type="submit"
                className="w-full"
                disabled={createCaseFieldIsPending}
              >
                Create Field
              </Button>
            </form>
          </Form>
        </div>
      </div>

      <AlertDialog
        open={isDeleteDialogOpen}
        onOpenChange={setIsDeleteDialogOpen}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete Field</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete the field{" "}
              <strong>{fieldToDelete}</strong>? This action cannot be undone and
              will delete all existing values for this field across all cases.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleteCaseFieldIsPending}>
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={confirmDeleteField}
              disabled={deleteCaseFieldIsPending}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
