import { zodResolver } from "@hookform/resolvers/zod"
import { Check, Edit2, X } from "lucide-react"
import type React from "react"
import { useState } from "react"
import { useForm } from "react-hook-form"
import * as z from "zod"
import type { RunbookRead, RunbookUpdate } from "@/client"

import { Button } from "@/components/ui/button"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { toast } from "@/components/ui/use-toast"

const aliasFormSchema = z.object({
  alias: z
    .string()
    .regex(/^[a-zA-Z0-9_-]+$/, {
      message: "Only letters, numbers, hyphens, and underscores allowed",
    })
    .min(3, { message: "Must be at least 3 characters" })
    .max(50, { message: "Must be at most 50 characters" })
    .optional()
    .nullable()
    .or(z.literal("")),
})
type AliasFormSchema = z.infer<typeof aliasFormSchema>

interface RunbookInlineAliasEditorProps {
  runbookData: RunbookRead
  updateRunbook: (params: {
    runbookId: string
    request: RunbookUpdate
  }) => Promise<RunbookRead>
}

export function RunbookInlineAliasEditor({
  runbookData,
  updateRunbook,
}: RunbookInlineAliasEditorProps) {
  const [isEditing, setIsEditing] = useState(false)
  const form = useForm<AliasFormSchema>({
    resolver: zodResolver(aliasFormSchema),
    defaultValues: {
      alias: runbookData?.alias || "",
    },
  })

  const handleAliasSubmit = async (values: AliasFormSchema) => {
    const newAlias = values.alias || null
    if (newAlias === runbookData?.alias) {
      setIsEditing(false)
      return // No changes to save
    }
    try {
      await updateRunbook({
        runbookId: runbookData.id,
        request: { alias: newAlias },
      })
      toast({
        title: "Runbook alias updated",
        description: newAlias
          ? `The runbook's alias is now '${newAlias}'`
          : "The runbook's alias has been removed",
      })
      setIsEditing(false)
    } catch (error) {
      console.error("Failed to update runbook alias:", error)
      toast({
        title: "Failed to update alias",
        description:
          "The alias might already be in use. Please try a different one.",
        variant: "destructive",
      })
      // Reset the form to the original value on error
      form.setValue("alias", runbookData?.alias || "")
    }
  }

  const handleCancel = () => {
    form.setValue("alias", runbookData?.alias || "")
    setIsEditing(false)
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault()
      form.handleSubmit(handleAliasSubmit)()
    } else if (e.key === "Escape") {
      handleCancel()
    }
  }

  if (!isEditing) {
    return (
      <div className="flex items-start gap-2 text-sm text-muted-foreground">
        <span className="leading-6">Alias:</span>
        <div className="min-h-[24px]">
          {runbookData.alias ? (
            <button
              onClick={() => setIsEditing(true)}
              className="h-6 font-mono px-1.5 rounded-md bg-muted hover:bg-muted/80 transition-colors inline-flex items-center"
            >
              {runbookData.alias}
            </button>
          ) : (
            <button
              onClick={() => setIsEditing(true)}
              className="h-6 px-1.5 text-muted-foreground/60 hover:text-muted-foreground transition-colors inline-flex items-center gap-1"
            >
              <Edit2 className="size-3" />
              <span>Add alias</span>
            </button>
          )}
        </div>
      </div>
    )
  }

  return (
    <Form {...form}>
      <form
        onSubmit={form.handleSubmit(handleAliasSubmit)}
        className="flex items-start gap-2"
      >
        <span className="text-sm text-muted-foreground leading-6">Alias:</span>
        <FormField
          control={form.control}
          name="alias"
          render={({ field }) => (
            <FormItem className="space-y-0">
              <div className="relative">
                <FormControl>
                  <div className="flex items-center gap-1">
                    <Input
                      {...field}
                      value={field.value || ""}
                      placeholder="e.g. incident-response"
                      className="h-6 px-1.5 py-0 text-sm font-mono w-36 border-input/50 focus:border-input"
                      autoFocus
                      onKeyDown={handleKeyDown}
                    />
                    <Button
                      type="submit"
                      size="icon"
                      variant="ghost"
                      className="h-6 w-6"
                    >
                      <Check className="size-3" />
                    </Button>
                    <Button
                      type="button"
                      size="icon"
                      variant="ghost"
                      className="h-6 w-6"
                      onClick={handleCancel}
                    >
                      <X className="size-3" />
                    </Button>
                  </div>
                </FormControl>
                <FormMessage className="absolute top-full left-0 text-xs mt-1" />
              </div>
            </FormItem>
          )}
        />
      </form>
    </Form>
  )
}
