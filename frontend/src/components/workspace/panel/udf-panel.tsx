"use client"

import "react18-json-view/src/style.css"

import React, { useState } from "react"
import {
  ApiError,
  UDFArgsValidationResponse,
  udfsValidateUdfArgs,
} from "@/client"
import Editor from "@monaco-editor/react"
import {
  AlertTriangleIcon,
  LayoutListIcon,
  SaveIcon,
  SettingsIcon,
  Shapes,
} from "lucide-react"
import { Controller, FieldValues, FormProvider, useForm } from "react-hook-form"
import { type Node } from "reactflow"
import YAML from "yaml"

import { usePanelAction } from "@/lib/hooks"
import { useUDFSchema } from "@/lib/udf"
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
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
import { UDFNodeData } from "@/components/workspace/canvas/udf-node"

type UDFFormSchema = {
  title?: string
  description?: string
  inputs?: string
}
export function UDFActionPanel<T extends Record<string, unknown>>({
  node,
  workflowId,
}: {
  node: Node<UDFNodeData>
  workflowId: string
}) {
  const {
    action,
    isLoading: actionLoading,
    mutateAsync: updateAction,
  } = usePanelAction(node.id, workflowId)
  const udfKey = node.data.type
  const { udf, isLoading: schemaLoading } = useUDFSchema(udfKey)
  const methods = useForm<UDFFormSchema>({
    values: {
      title: action?.title,
      description: action?.description,
      inputs: YAML.stringify(action?.inputs ?? ""),
    },
  })

  const [validationErrors, setValidationErrors] =
    useState<UDFArgsValidationResponse | null>(null)

  if (schemaLoading || actionLoading) {
    return <CenteredSpinner />
  }
  if (!udf) {
    return (
      <div className="flex h-full items-center justify-center space-x-2 p-4">
        <AlertNotification
          level="error"
          message={`Could not load UDF schema '${udfKey}'.`}
        />
      </div>
    )
  }
  if (!action) {
    return (
      <div className="flex h-full items-center justify-center space-x-2 p-4">
        <AlertNotification
          level="error"
          message={`Error orccurred loading action.`}
        />
      </div>
    )
  }

  const onSubmit = async (values: FieldValues) => {
    const actionInputs = YAML.parse(values.inputs)
    try {
      const validateResponse = await udfsValidateUdfArgs({
        udfKey: udf.key,
        requestBody: actionInputs,
      })
      console.log("Validation passed", validateResponse)
      if (!validateResponse.ok) {
        console.error("Validation failed", validateResponse)
        setValidationErrors(validateResponse)
        toast({
          title: "Validation Error",
          description: "Failed to validate action inputs",
        })
      } else {
        setValidationErrors(null)
        const params = {
          title: values.title as string,
          description: values.description as string,
          inputs: actionInputs,
        } as FieldValues
        console.log("Submitting action form", params)
        await updateAction(params as T)
      }
    } catch (error) {
      if (error instanceof ApiError) {
        console.error("Application failed to validate UDF", error.body)
      } else {
        console.error("Validation failed, unknown error", error)
      }
    }
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
              <h3 className="p-4">
                <div className="flex w-full items-center space-x-4">
                  {getIcon(udf.key, {
                    className: "size-10 p-2",
                    flairsize: "md",
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

            {/* Schema */}
            <AccordionItem value="action-schema">
              <AccordionTrigger className="px-4 text-xs font-bold tracking-wide">
                <div className="flex items-center">
                  <Shapes className="mr-3 size-4" />
                  <span>Schema</span>
                </div>
              </AccordionTrigger>
              <AccordionContent className="space-y-4">
                {/* UDF secrets */}
                <div className="space-y-4 px-4">
                  {udf.secrets ? (
                    <div className="text-xs text-muted-foreground">
                      <span>This action requires the following secrets:</span>
                      <Table>
                        <TableHeader>
                          <TableRow className="h-6  text-xs capitalize">
                            <TableHead className="font-bold" colSpan={1}>
                              Secret Name
                            </TableHead>
                            <TableHead className="font-bold" colSpan={1}>
                              Secret Keys
                            </TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {udf.secrets.map((secret, idx) => (
                            <TableRow
                              key={idx}
                              className="font-mono text-xs tracking-tight text-muted-foreground"
                            >
                              <TableCell>{secret.name}</TableCell>
                              <TableCell>{secret.keys.join(", ")}</TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </div>
                  ) : (
                    <span className="text-xs text-muted-foreground">
                      No secrets required.
                    </span>
                  )}
                </div>
                {/* UDF inputs */}
                <div className="space-y-4 px-4">
                  <span className="text-xs text-muted-foreground">
                    Hover over the fields to see more details.
                  </span>
                  <JSONSchemaTable schema={udf.args} />
                </div>
              </AccordionContent>
            </AccordionItem>

            {/* Inputs */}
            <AccordionItem value="action-inputs">
              <AccordionTrigger className="px-4 text-xs font-bold tracking-wide">
                <div className="flex items-center">
                  <LayoutListIcon className="mr-3 size-4" />
                  <span>Inputs</span>
                </div>
              </AccordionTrigger>
              <AccordionContent>
                <div className="flex flex-col space-y-4 px-4">
                  {validationErrors && (
                    <div className="flex items-center space-x-2">
                      <AlertTriangleIcon className="size-4 fill-rose-500 stroke-white" />
                      <span className="text-xs text-rose-500">
                        Validation errors occurred, please see below.
                      </span>
                    </div>
                  )}
                  <span className="text-xs text-muted-foreground">
                    Edit the action inputs in YAML below.
                  </span>
                  <Controller
                    name="inputs"
                    control={methods.control}
                    render={({ field }) => (
                      <div className="h-96 w-full border">
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
                  {validationErrors && (
                    <div className="rounded-md border-2 border-rose-500 bg-rose-100 p-4 font-mono text-xs text-rose-600">
                      <span className="font-bold">Validation Errors</span>
                      <Separator className="my-2 bg-rose-400" />
                      <span>{validationErrors.message}</span>
                      <pre>{YAML.stringify(validationErrors.detail)}</pre>
                    </div>
                  )}
                </div>
              </AccordionContent>
            </AccordionItem>
          </Accordion>
        </form>
      </FormProvider>
    </div>
  )
}
