"use client"

import JsonView from "react18-json-view"

import "react18-json-view/src/style.css"

import React from "react"
import {
  BracesIcon,
  LayoutListIcon,
  SaveIcon,
  SettingsIcon,
  Shapes,
  ViewIcon,
} from "lucide-react"
import { FieldValues, FormProvider, useForm } from "react-hook-form"

import { PanelAction, useActionInputs } from "@/lib/hooks"
import { ErrorDetails, useUDFSchema, validateUDFArgs } from "@/lib/udf"
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"
import { Button } from "@/components/ui/button"
import {
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { Separator } from "@/components/ui/separator"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import { getIcon } from "@/components/icons"
import { JSONSchemaTable } from "@/components/jsonschema-table"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"

export function UDFActionForm({
  panelAction: {
    action,
    isLoading: actionLoading,
    mutateAsync,
    queryClient,
    queryKeys,
  },
  type: key,
}: {
  panelAction: PanelAction<any>
  type: string
  actionId: string
  workflowId: string
}) {
  const { udf, isLoading: schemaLoading } = useUDFSchema(key)
  const methods = useForm<{ title?: string; description?: string }>({
    values: {
      title: action?.title,
      description: action?.description,
    },
  })
  const { actionInputs, setActionInputs } = useActionInputs(action)
  const [JSONViewerrors, setJSONViewErrors] = React.useState<
    ErrorDetails[] | undefined
  >(undefined)

  if (schemaLoading || actionLoading) {
    return <CenteredSpinner />
  }
  if (!udf || !action) {
    return (
      <div className="flex h-full items-center justify-center space-x-2 p-4">
        <div className="space-y-2">
          <AlertNotification
            level="info"
            message={`UDF type '${key}' is not yet supported.`}
            reset={() =>
              queryClient.invalidateQueries({
                queryKey: queryKeys.selectedAction,
              })
            }
          />
        </div>
      </div>
    )
  }

  const onSubmit = async (values: FieldValues) => {
    const validateResponse = await validateUDFArgs(udf.key, actionInputs)
    if (!validateResponse.ok) {
      const detail = validateResponse.detail
      console.log("Validation failed", validateResponse)
      setJSONViewErrors(detail ?? undefined)
      return
    }
    setJSONViewErrors([])

    const params = {
      title: values.title,
      description: values.description,
      inputs: actionInputs,
    }
    console.log("Submitting action form", params)
    await mutateAsync(params)
  }

  return (
    <div className="size-full overflow-auto">
      <FormProvider {...methods}>
        <form
          onSubmit={methods.handleSubmit(onSubmit)}
          className="flex max-w-full flex-col overflow-auto"
        >
          <div className="grid grid-cols-3">
            <div className="col-span-2 overflow-hidden">
              <h3 className="p-4 px-4">
                <div className="flex w-full items-center space-x-4">
                  {getIcon(udf.key, {
                    className: "size-10 p-2",
                    flairSize: "md",
                  })}
                  <div className="flex w-full flex-1 justify-between space-x-12">
                    <div className="flex flex-col">
                      <div className="flex w-full items-center justify-between text-xs font-medium leading-none">
                        <div className="flex w-full">{action.title}</div>
                      </div>
                      <p className="mt-2 text-xs text-muted-foreground">
                        {action.description || (
                          <span className="italic">No description</span>
                        )}
                      </p>
                    </div>
                  </div>
                </div>
              </h3>
            </div>
            <div className="flex justify-end space-x-2 p-4">
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button type="submit" size="icon">
                    <SaveIcon className="size-4" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Save</TooltipContent>
              </Tooltip>
            </div>
          </div>
          <Separator />
          {/* Metadata */}
          <Accordion
            type="multiple"
            defaultValue={["action-settings", "action-schema", "action-inputs"]}
          >
            <AccordionItem value="action-settings">
              <AccordionTrigger className="px-4 text-xs font-bold tracking-wide">
                <div className="flex items-center">
                  <SettingsIcon className="mr-3 size-4" />
                  <span>General</span>
                </div>
              </AccordionTrigger>
              {/* General settings for the action */}
              <AccordionContent>
                <div className="my-4 space-y-2 px-4">
                  <FormField
                    control={methods.control}
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
                    control={methods.control}
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
                </div>
              </AccordionContent>
            </AccordionItem>
            <AccordionItem value="action-schema">
              <AccordionTrigger className="px-4 text-xs font-bold tracking-wide">
                <div className="flex items-center">
                  <Shapes className="mr-3 size-4" />
                  <span>Schema</span>
                </div>
              </AccordionTrigger>
              <AccordionContent>
                <div className="px-4">
                  <JSONSchemaTable schema={udf.args} />
                </div>
              </AccordionContent>
            </AccordionItem>
            <AccordionItem value="action-inputs">
              <AccordionTrigger className="px-4 text-xs font-bold tracking-wide">
                <div className="flex items-center">
                  <LayoutListIcon className="mr-3 size-4" />
                  <span>Inputs</span>
                </div>
              </AccordionTrigger>
              <AccordionContent>
                <div className="space-y-4 px-4">
                  <ActionInputs
                    inputs={actionInputs}
                    setInputs={setActionInputs}
                    errors={JSONViewerrors}
                  />
                </div>
              </AccordionContent>
            </AccordionItem>
          </Accordion>
        </form>
      </FormProvider>
    </div>
  )
}

export function ActionInputs({
  inputs,
  setInputs,
  errors,
}: {
  inputs: any
  setInputs: (obj: any) => void
  errors?: ErrorDetails[]
}) {
  return (
    <Tabs defaultValue="json">
      <div className="flex flex-1 justify-end">
        <TabsList className="mb-4">
          <TabsTrigger className="text-xs" value="json">
            <BracesIcon className="mr-2 size-3.5" />
            <span>JSON</span>
          </TabsTrigger>
          <TabsTrigger className="text-xs" value="form">
            <ViewIcon className="mr-2 size-3.5" />
            <span>View</span>
          </TabsTrigger>
        </TabsList>
      </div>
      <TabsContent value="json">
        <div className="w-full rounded-md border p-4">
          {/* The json contains the view into the data */}
          <JsonView
            displaySize
            editable
            enableClipboard
            src={inputs}
            onChange={(params) => {
              console.log("changed", params)
              setInputs(params.src)
            }}
            className="text-xs"
          />
        </div>
        <div className="w-full space-y-2 py-4">
          {errors?.length == 0 ? (
            <AlertNotification
              className="text-xs"
              level="success"
              message="Validated successfully!"
            />
          ) : (
            errors?.map((error, idx) => {
              const msg = `${error.type}: ${error.msg} @ \`${error.loc[0]}\`. Received input ${error.input}`
              return (
                <AlertNotification
                  className="text-xs"
                  key={idx}
                  level="error"
                  message={msg}
                />
              )
            })
          )}
        </div>
      </TabsContent>
      <TabsContent value="form">
        <div className="justify-center space-y-4 text-center text-xs italic text-muted-foreground">
          Action forms are being revamped. Please use the JSON editor for now.
        </div>
      </TabsContent>
    </Tabs>
  )
}
