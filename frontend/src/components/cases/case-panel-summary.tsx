import React from "react"
import { CaseRead, CaseUpdate } from "@/client"
import { zodResolver } from "@hookform/resolvers/zod"
import { useForm } from "react-hook-form"
import * as z from "zod"

import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"

const summaryFormSchema = z.object({
  summary: z.string().min(1, { message: "Summary is required" }),
})
type SummaryFormSchema = z.infer<typeof summaryFormSchema>

interface CasePanelSummaryProps {
  caseData: CaseRead
  updateCase: (caseData: CaseUpdate) => Promise<void>
}

export function CasePanelSummary({
  caseData,
  updateCase,
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

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
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
                <Input
                  {...field}
                  value={field.value || ""}
                  variant="flat"
                  className="w-full px-1 text-xl font-semibold"
                  onBlur={() => handleSummarySubmit(form.getValues())}
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
