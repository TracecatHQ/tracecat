"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type { WorkspaceRead } from "@/client"
import { Button } from "@/components/ui/button"
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
} from "@/components/ui/form"
import { Switch } from "@/components/ui/switch"
import { useWorkspaceSettings } from "@/lib/hooks"

const caseSettingsSchema = z.object({
  close_sub_cases_with_parent: z.boolean(),
})

type CaseSettingsForm = z.infer<typeof caseSettingsSchema>

interface WorkspaceCaseSettingsProps {
  workspace: WorkspaceRead
}

export function WorkspaceCaseSettings({
  workspace,
}: WorkspaceCaseSettingsProps) {
  const { updateWorkspace, isUpdating } = useWorkspaceSettings(workspace.id)

  const form = useForm<CaseSettingsForm>({
    resolver: zodResolver(caseSettingsSchema),
    defaultValues: {
      close_sub_cases_with_parent:
        workspace.settings?.close_sub_cases_with_parent ?? true,
    },
  })

  async function onSubmit(values: CaseSettingsForm) {
    await updateWorkspace({ settings: values })
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6">
        <FormField
          control={form.control}
          name="close_sub_cases_with_parent"
          render={({ field }) => (
            <FormItem className="flex flex-row items-center justify-between rounded-lg border p-4">
              <div className="space-y-0.5">
                <FormLabel>Close sub-cases with parent</FormLabel>
                <FormDescription>
                  When a parent case is closed or resolved, close its open
                  sub-cases with the same status and fill their empty
                  required-on-closure fields from the parent.
                </FormDescription>
              </div>
              <FormControl>
                <Switch
                  checked={field.value}
                  onCheckedChange={field.onChange}
                />
              </FormControl>
            </FormItem>
          )}
        />

        <Button type="submit" disabled={isUpdating} size="sm">
          {isUpdating ? "Saving..." : "Save"}
        </Button>
      </form>
    </Form>
  )
}
