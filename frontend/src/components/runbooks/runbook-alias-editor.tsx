import { zodResolver } from "@hookform/resolvers/zod"
import type React from "react"
import { useForm } from "react-hook-form"
import * as z from "zod"
import type { RunbookRead, RunbookUpdate } from "@/client"

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
import { toast } from "@/components/ui/use-toast"

const aliasFormSchema = z.object({
  alias: z
    .string()
    .regex(/^[a-zA-Z0-9_-]+$/, {
      message:
        "Alias can only contain letters, numbers, hyphens, and underscores",
    })
    .min(3, { message: "Alias must be at least 3 characters" })
    .max(50, { message: "Alias must be at most 50 characters" })
    .nullish(),
})
type AliasFormSchema = z.infer<typeof aliasFormSchema>

interface RunbookAliasEditorProps {
  runbookData: RunbookRead
  updateRunbook: (params: {
    runbookId: string
    request: RunbookUpdate
  }) => Promise<RunbookRead>
}

export function RunbookAliasEditor({
  runbookData,
  updateRunbook,
}: RunbookAliasEditorProps) {
  const form = useForm<AliasFormSchema>({
    resolver: zodResolver(aliasFormSchema),
    defaultValues: {
      alias: runbookData?.alias || "",
    },
  })

  const handleAliasSubmit = async (values: AliasFormSchema) => {
    if (values.alias === runbookData?.alias) {
      return // No changes to save
    }
    try {
      await updateRunbook({
        runbookId: runbookData.id,
        request: { alias: values.alias || null },
      })
      toast({
        title: "Runbook alias updated",
        description: "The runbook alias has been updated successfully.",
      })
    } catch (error) {
      console.error("Failed to update runbook alias:", error)
      toast({
        title: "Failed to update alias",
        description:
          "An error occurred while updating the runbook alias. Please try again.",
        variant: "destructive",
      })
      // Reset the form to the original value on error
      form.setValue("alias", runbookData?.alias || "")
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault()
      form.handleSubmit(handleAliasSubmit)()
      e.currentTarget.blur()
    }
  }

  return (
    <Form {...form}>
      <form
        onSubmit={form.handleSubmit(handleAliasSubmit)}
        className="space-y-2"
      >
        <FormField
          control={form.control}
          name="alias"
          render={({ field }) => (
            <FormItem>
              <FormLabel className="text-sm font-medium">Alias</FormLabel>
              <FormControl>
                <Input
                  {...field}
                  value={field.value || ""}
                  placeholder="e.g. incident-response"
                  className="font-mono text-sm"
                  onBlur={() => form.handleSubmit(handleAliasSubmit)()}
                  onKeyDown={handleKeyDown}
                />
              </FormControl>
              <FormDescription className="text-xs">
                A unique identifier for quick reference (letters, numbers,
                hyphens, and underscores only)
              </FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />
      </form>
    </Form>
  )
}
