"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { FormProvider, useForm } from "react-hook-form"
import z from "zod"

import { actionRunSchema, workflowRunSchema } from "@/types/schemas"
import { stringToJSONSchema } from "@/types/validators"
import { ConsolePanel } from "@/components/console/control-panel"
import { ConsoleFeed } from "@/components/console/event-feed"

const hasTypeSchema = z.object({
  type: z.enum(["action_run", "workflow_run"]),
})
const arbitraryKeyValuePairsSchema = z.record(z.any())
export const consoleEventSchema = hasTypeSchema.and(
  arbitraryKeyValuePairsSchema
)
export type GenericConsoleEvent = z.infer<typeof consoleEventSchema>

const workflowControlsFormSchema = z.object({
  payload: stringToJSONSchema, // json
  actionKey: z.string({ required_error: "Please select a webhook." }),
  mimeType: z.string({ required_error: "Please select an input data type." }),
})

export type WorkflowControlsForm = z.infer<typeof workflowControlsFormSchema>

export const consoleSchemaMap = {
  action_run: actionRunSchema,
  workflow_run: workflowRunSchema,
}

export type ConsoleSchemaKey = keyof typeof consoleSchemaMap

export function Console() {
  const methods = useForm<WorkflowControlsForm>({
    resolver: zodResolver(workflowControlsFormSchema),
    defaultValues: {
      actionKey: "",
      mimeType: "",
      payload: "",
    },
  })

  return (
    <FormProvider {...methods}>
      <form className="h-full overflow-auto">
        <main className="grid h-full w-full flex-1 items-center gap-4 overflow-auto p-4 md:grid-cols-2 lg:grid-cols-6">
          <ConsolePanel className="col-span-2" />
          <ConsoleFeed className="col-span-4" />
        </main>
      </form>
    </FormProvider>
  )
}
