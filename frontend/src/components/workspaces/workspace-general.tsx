"use client"

import "react18-json-view/src/style.css"

import React from "react"
import { WorkspaceResponse } from "@/client"
import { useAuth } from "@/providers/auth"
import { zodResolver } from "@hookform/resolvers/zod"
import { FormProvider, useForm } from "react-hook-form"
import { z } from "zod"

import { useWorkspace } from "@/lib/hooks"
import { Button } from "@/components/ui/button"
import {
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"

const workspaceConfigFormSchema = z.object({
  name: z.string(),
})
type WorkspaceConfigFormSchema = z.infer<typeof workspaceConfigFormSchema>

export function WprkspaceGeneralSettings({
  workspace,
}: {
  workspace: WorkspaceResponse
}) {
  const { user } = useAuth()
  const { updateWorkspace } = useWorkspace()
  const hasPermissions = user?.is_superuser || user?.role === "admin"

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
    } catch (e) {
      console.error("Error updating workspace")
    }
  }

  return (
    <div className="size-full overflow-auto">
      <FormProvider {...methods}>
        <form
          onSubmit={methods.handleSubmit(onSubmit)}
          className="flex flex-col overflow-auto"
        >
          <div className="my-4 w-1/2 space-y-4 px-4">
            <FormField
              control={methods.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel className="text-xs">Workspace Name</FormLabel>
                  <FormControl>
                    <Input
                      className="text-xs"
                      placeholder="Workspace Name"
                      {...field}
                      disabled={!hasPermissions}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <Button type="submit" variant="default">
              Update Workspace
            </Button>
          </div>
        </form>
      </FormProvider>
    </div>
  )
}
