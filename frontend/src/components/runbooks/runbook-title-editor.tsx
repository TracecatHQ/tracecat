import { zodResolver } from "@hookform/resolvers/zod"
import type React from "react"
import { useForm } from "react-hook-form"
import * as z from "zod"
import type { PromptRead, PromptUpdate } from "@/client"

import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"

const titleFormSchema = z.object({
  title: z.string().min(1, { message: "Title is required" }),
})
type TitleFormSchema = z.infer<typeof titleFormSchema>

interface RunbookTitleEditorProps {
  promptData: PromptRead
  updatePrompt: (params: {
    promptId: string
    request: PromptUpdate
  }) => Promise<PromptRead>
}

export function RunbookTitleEditor({
  promptData,
  updatePrompt,
}: RunbookTitleEditorProps) {
  const form = useForm<TitleFormSchema>({
    resolver: zodResolver(titleFormSchema),
    defaultValues: {
      title: promptData?.title || "",
    },
  })

  const handleTitleSubmit = async (values: TitleFormSchema) => {
    if (values.title === promptData?.title) {
      return // No changes to save
    }
    await updatePrompt({
      promptId: promptData.id,
      request: { title: values.title },
    })
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
                  className="-mx-1 w-full px-1 text-xl font-semibold"
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
