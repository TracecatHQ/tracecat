"use client"

import "react18-json-view/src/style.css"

import { zodResolver } from "@hookform/resolvers/zod"
import { FormProvider, useForm } from "react-hook-form"
import { z } from "zod"
import type { WorkspaceRead } from "@/client"
import { Button } from "@/components/ui/button"
import {
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { useAuth } from "@/providers/auth"
import { useWorkspace } from "@/providers/workspace"

const workspaceConfigFormSchema = z.object({
  name: z.string(),
})
type WorkspaceConfigFormSchema = z.infer<typeof workspaceConfigFormSchema>

export function WorkspaceGeneralSettings({
  workspace,
}: {
  workspace: WorkspaceRead
}) {
  const { user } = useAuth()
  const { updateWorkspace } = useWorkspace()

  const methods = useForm({
    resolver: zodResolver(workspaceConfigFormSchema),
    defaultValues: {
      name: workspace.name,
    },
  })
  const onSubmit = async (values: WorkspaceConfigFormSchema) => {
    console.log("SUBMIT WORKSPACE CONFIG", values)
    try {
      await updateWorkspace({ name: values.name })
    } catch (_e) {
      console.error("Error updating workspace")
    }
  }

  return (
    <FormProvider {...methods}>
      <form
        onSubmit={methods.handleSubmit(onSubmit)}
        className="flex items-end gap-4"
      >
        <div className="w-[400px]">
          <FormField
            control={methods.control}
            name="name"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Workspace name</FormLabel>
                <FormControl>
                  <Input
                    placeholder="Workspace name"
                    {...field}
                    disabled={!user?.isPrivileged()}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
        </div>
        <Button type="submit" variant="default">
          Update workspace
        </Button>
      </form>
    </FormProvider>
  )
}
