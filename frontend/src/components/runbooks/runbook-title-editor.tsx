import { zodResolver } from "@hookform/resolvers/zod"
import type React from "react"
import { useForm } from "react-hook-form"
import * as z from "zod"
import type { RunbookRead, RunbookUpdate } from "@/client"

import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { toast } from "@/components/ui/use-toast"

const titleFormSchema = z.object({
  title: z.string().min(1, { message: "Title is required" }),
})
type TitleFormSchema = z.infer<typeof titleFormSchema>

interface RunbookTitleEditorProps {
  runbookData: RunbookRead
  updateRunbook: (params: {
    runbookId: string
    request: RunbookUpdate
  }) => Promise<RunbookRead>
}

export function RunbookTitleEditor({
  runbookData,
  updateRunbook,
}: RunbookTitleEditorProps) {
  const form = useForm<TitleFormSchema>({
    resolver: zodResolver(titleFormSchema),
    defaultValues: {
      title: runbookData?.title || "",
    },
  })

  const handleTitleSubmit = async (values: TitleFormSchema) => {
    if (values.title === runbookData?.title) {
      return // No changes to save
    }
    try {
      await updateRunbook({
        runbookId: runbookData.id,
        request: { title: values.title },
      })
      toast({
        title: "Title updated",
        description: "The runbook title has been updated successfully.",
      })
    } catch (error) {
      console.error("Failed to update runbook title:", error)
      toast({
        title: "Failed to update title",
        description: "An error occurred while updating the runbook title. Please try again.",
        variant: "destructive",
      })
      // Reset the form to the original value on error
      form.setValue("title", runbookData?.title || "")
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault()
      form.handleSubmit(handleTitleSubmit)()
      e.currentTarget.blur()
    }
  }

  return (
    <Form {...form}>
      <form
        onSubmit={form.handleSubmit(handleTitleSubmit)}
        className="space-y-2"
      >
        <FormField
          control={form.control}
          name="title"
          render={({ field }) => (
            <FormItem>
              <FormControl>
                <Input
                  {...field}
                  value={field.value || ""}
                  variant="flat"
                  className="-mx-1 w-full px-2 text-2xl font-semibold"
                  onBlur={() => form.handleSubmit(handleTitleSubmit)()}
                  onKeyDown={handleKeyDown}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
      </form>
    </Form>
  )
}
