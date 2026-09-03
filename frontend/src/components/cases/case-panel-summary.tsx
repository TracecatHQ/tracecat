import { zodResolver } from "@hookform/resolvers/zod"
import type React from "react"
import { useForm } from "react-hook-form"
import * as z from "zod"
import type { CaseRead, CaseUpdate } from "@/client"

import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"

const summaryFormSchema = z.object({
  summary: z.string().min(1, { message: "Summary is required" }),
})
type SummaryFormSchema = z.infer<typeof summaryFormSchema>

interface CasePanelSummaryProps {
  caseData: CaseRead
  updateCase: (caseData: CaseUpdate) => Promise<void>
  compact?: boolean
}

export function CasePanelSummary({
  caseData,
  updateCase,
  compact = false,
}: CasePanelSummaryProps) {
  const form = useForm<SummaryFormSchema>({
    resolver: zodResolver(summaryFormSchema),
    values: {
      summary: caseData?.summary || "",
    },
  })
  const handleSummarySubmit = async (values: SummaryFormSchema) => {
    await updateCase({ summary: values.summary })
  }

  const handleKeyDown = (
    e: React.KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>
  ) => {
    if (e.key === "Enter") {
      if (e.currentTarget instanceof HTMLTextAreaElement && e.shiftKey) {
        return
      }
      e.preventDefault()
      handleSummarySubmit(form.getValues())
      e.currentTarget.blur()
    }
  }

  return (
    <Form {...form}>
      <form
        onSubmit={form.handleSubmit(handleSummarySubmit)}
        className="space-y-2"
      >
        <FormField
          control={form.control}
          name="summary"
          render={({ field }) => (
            <FormItem>
              <FormControl>
                {compact ? (
                  <Textarea
                    {...field}
                    value={field.value || ""}
                    rows={2}
                    className="min-h-0 max-w-full resize-none border-transparent bg-transparent px-0 py-1 text-lg font-semibold shadow-none focus-visible:ring-0"
                    onBlur={() => handleSummarySubmit(form.getValues())}
                    onKeyDown={handleKeyDown}
                  />
                ) : (
                  <Input
                    {...field}
                    value={field.value || ""}
                    variant="flat"
                    className="-mx-1 w-full px-1 text-xl font-semibold"
                    onBlur={() => handleSummarySubmit(form.getValues())}
                    onKeyDown={handleKeyDown}
                  />
                )}
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
      </form>
    </Form>
  )
}
