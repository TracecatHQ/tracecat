"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useQueryClient } from "@tanstack/react-query"
import { Plus } from "lucide-react"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import { casesCreateField } from "@/client"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
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
import { toast } from "@/components/ui/use-toast"
import { SqlTypeEnum } from "@/lib/tables"
import { useWorkspace } from "@/providers/workspace"

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

export function AddCustomField() {
  const { workspaceId } = useWorkspace()
  const queryClient = useQueryClient()
  const [showDialog, setShowDialog] = useState(false)
  const [isSubmitting, setIsSubmitting] = useState(false)

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
    setIsSubmitting(true)
    try {
      await casesCreateField({
        workspaceId,
        requestBody: {
          name: data.name,
          type: data.type,
          nullable: data.nullable,
          default: data.default || null,
        },
      })

      queryClient.invalidateQueries({
        queryKey: ["case-fields", workspaceId],
      })

      toast({
        title: "Field created",
        description: "The case field was created successfully.",
      })

      form.reset()
      setShowDialog(false)
    } catch (error) {
      console.error("Failed to create case field", error)
      toast({
        title: "Error creating field",
        description: "Failed to create the case field. Please try again.",
        variant: "destructive",
      })
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <Dialog open={showDialog} onOpenChange={setShowDialog}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm" className="h-7 bg-white">
          <Plus className="mr-1 h-3.5 w-3.5" />
          Add field
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-[425px]">
        <DialogHeader>
          <DialogTitle>Add custom field</DialogTitle>
          <DialogDescription>
            Create a new custom field for cases.
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Field ID</FormLabel>
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
                      {SqlTypeEnum.filter((type) => type !== "JSONB").map(
                        (type) => (
                          <SelectItem key={type} value={type}>
                            {type}
                          </SelectItem>
                        )
                      )}
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

            <DialogFooter>
              <Button type="submit" disabled={isSubmitting}>
                Create Field
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}
