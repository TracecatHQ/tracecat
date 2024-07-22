"use client"

import * as React from "react"
import { zodResolver } from "@hookform/resolvers/zod"

import "@radix-ui/react-dialog"

import { useRouter } from "next/navigation"
import { ApiError } from "@/client"
import { useWorkflow } from "@/providers/workflow"
import { Editor } from "@monaco-editor/react"
import { FileInputIcon, SaveIcon, Settings2Icon } from "lucide-react"
import { Controller, useForm } from "react-hook-form"
import YAML from "yaml"
import { z } from "zod"

import { Workflow } from "@/types/schemas"
import { isEmptyObjectOrNullish } from "@/lib/utils"
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"
import { Button } from "@/components/ui/button"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { Separator } from "@/components/ui/separator"
import { Textarea } from "@/components/ui/textarea"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import { CopyButton } from "@/components/copy-button"
import { CenteredSpinner } from "@/components/loading/spinner"
import { WorkflowSettings } from "@/components/workspace/panel/workflow/settings"

const workflowFormSchema = z.object({
  title: z.string(),
  description: z.string(),
  staticInputs: z.string(),
})

type TWorkflowForm = z.infer<typeof workflowFormSchema>

export function WorkflowForm({
  workflow,
}: {
  workflow: Workflow
}): React.JSX.Element {
  const router = useRouter()
  const { update } = useWorkflow()
  const form = useForm<TWorkflowForm>({
    resolver: zodResolver(workflowFormSchema),
    defaultValues: {
      title: workflow.title || "",
      description: workflow.description || "",
      staticInputs: isEmptyObjectOrNullish(workflow.static_inputs)
        ? ""
        : YAML.stringify(workflow.static_inputs),
    },
  })

  const onSubmit = async (values: TWorkflowForm) => {
    let params
    try {
      params = {
        ...values,
        static_inputs: YAML.parse(values.staticInputs),
      }
    } catch (error) {
      console.error("Error parsing static inputs:", error)
      form.setError("staticInputs", { message: "Invalid YAML format" })
      return
    }
    console.log("Saving changes...")
    try {
      await update(params)
      toast({
        title: "Saved changes",
        description: "Workflow updated successfully.",
      })
      // Refresh the workflow data
      router.refresh()
    } catch (error) {
      if (error instanceof ApiError) {
        toast({
          title: "Error saving changes",
          description: error.message,
          variant: "destructive",
        })
      }
    }
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)}>
        {/* Save and Workflow Settings */}
        <div className="flex flex-1 justify-end gap-2 p-4">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button type="submit" size="icon">
                <SaveIcon className="size-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>Save Changes</TooltipContent>
          </Tooltip>
          <WorkflowSettings workflow={workflow} />
        </div>
        <Separator />
        {/* Workflow Settings */}
        <Accordion
          type="multiple"
          defaultValue={["workflow-settings", "workflow-static-inputs"]}
        >
          <AccordionItem value="workflow-settings">
            <AccordionTrigger className="px-4 text-xs font-bold tracking-wide">
              <div className="flex items-center">
                <Settings2Icon className="mr-3 size-4" />
                <span>Workflow</span>
              </div>
            </AccordionTrigger>
            <AccordionContent>
              <div className="my-4 space-y-2 px-4">
                <FormField
                  control={form.control}
                  name="title"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel className="text-xs">Name</FormLabel>
                      <FormControl>
                        <Input
                          className="text-xs"
                          placeholder="Name your workflow..."
                          {...field}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="description"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel className="text-xs">Description</FormLabel>
                      <FormControl>
                        <Textarea
                          className="text-xs"
                          placeholder="Describe your workflow..."
                          {...field}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <div className="space-y-2">
                  <FormLabel className="flex items-center gap-2 text-xs text-muted-foreground">
                    <span>Workflow ID</span>
                    <CopyButton
                      value={workflow.id}
                      toastMessage="Copied workflow ID to clipboard"
                    />
                  </FormLabel>
                  <div className="rounded-md border shadow-sm">
                    <Input
                      defaultValue={workflow.id}
                      className="rounded-md border-none text-xs shadow-none"
                      readOnly
                      disabled
                    />
                  </div>
                </div>
              </div>
            </AccordionContent>
          </AccordionItem>
          <AccordionItem value="workflow-static-inputs">
            <AccordionTrigger className="px-4 text-xs font-bold tracking-wide">
              <Tooltip>
                <TooltipTrigger asChild>
                  <div className="flex items-center">
                    <FileInputIcon className="mr-3 size-4" />
                    <span>Static Inputs</span>
                  </div>
                </TooltipTrigger>
                <TooltipContent>
                  Unchanging inputs that the workflow always has access to.
                </TooltipContent>
              </Tooltip>
            </AccordionTrigger>
            <AccordionContent>
              <div className="flex flex-col space-y-4 px-4">
                <span className="text-xs text-muted-foreground">
                  Edit the static workflow inputs in YAML below.
                </span>
                <Controller
                  name="staticInputs"
                  control={form.control}
                  render={({ field }) => (
                    <div className="h-48 w-full border">
                      <Editor
                        defaultLanguage="yaml"
                        value={field.value}
                        onChange={field.onChange}
                        height="100%"
                        theme="vs-light"
                        loading={<CenteredSpinner />}
                        options={{
                          tabSize: 2,
                          minimap: { enabled: false },
                          scrollbar: {
                            verticalScrollbarSize: 5,
                            horizontalScrollbarSize: 5,
                          },
                        }}
                      />
                    </div>
                  )}
                />
              </div>
            </AccordionContent>
          </AccordionItem>
        </Accordion>
      </form>
    </Form>
  )
}
