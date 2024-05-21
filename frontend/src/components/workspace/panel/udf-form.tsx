"use client"

import React from "react"
import { DevTool } from "@hookform/devtools"
import { CopyIcon } from "@radix-ui/react-icons"
import { CircleIcon, PlusCircle, Save, Trash2Icon } from "lucide-react"
import {
  ArrayPath,
  FieldPath,
  FieldValues,
  FormProvider,
  PathValue,
  useFieldArray,
  useForm,
  useFormContext,
} from "react-hook-form"
import SyntaxHighlighter from "react-syntax-highlighter"
import { atomOneDark } from "react-syntax-highlighter/dist/esm/styles/hljs"

import { Action } from "@/types/schemas"
import { useCustomAJVResolver, usePanelAction } from "@/lib/hooks"
import { FieldConfig, useUDFFormSchema } from "@/lib/udf"
import { cn, copyToClipboard, undoSlugify } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Separator } from "@/components/ui/separator"
import { Textarea } from "@/components/ui/textarea"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import { CollapsibleSection } from "@/components/collapsible-section"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { DELETE_BUTTON_STYLE } from "@/styles/tailwind"

///////////////////////////
// UDF Form Components
///////////////////////////

export function UDFForm({
  type: key,
  namespace,
  actionId,
  workflowId,
}: {
  type: string
  namespace: string
  actionId: string
  workflowId: string
}) {
  // 1. Load schema
  const {
    formConfig,
    formSchema,
    udf,
    isLoading: schemaLoading,
  } = useUDFFormSchema(key)

  // 2. Load any existing data
  const {
    action,
    isLoading: actionLoading,
    mutateAsync,
    queryClient,
    queryKeys,
  } = usePanelAction(actionId, workflowId)
  const resolver = useCustomAJVResolver(formSchema!)

  const methods = useForm({
    resolver,
    values: populateForm(action),
  })

  if (schemaLoading || actionLoading) {
    return <CenteredSpinner />
  }
  if (!formConfig || !formSchema || !udf || !action) {
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

  const onSubmit = methods.handleSubmit(async (values: FieldValues) => {
    console.log("Submitting UDF form", values)
    await mutateAsync(values)
  })

  const status = "online"
  type Schema = {
    [key: string]: any
  }
  return (
    <div className="flex flex-col overflow-auto">
      <DevTool control={methods.control} placement="top-left" />
      <FormProvider {...methods}>
        <form onSubmit={onSubmit} className="flex max-w-full overflow-auto">
          <div className="w-full space-y-4 p-4">
            <div className="flex w-full flex-col space-y-3 overflow-hidden">
              <h4 className="text-sm font-medium">Action Status</h4>
              <div className="flex justify-between">
                <Badge
                  variant="outline"
                  className={cn(
                    "px-4 py-1",
                    status === "online" ? "bg-green-100" : "bg-gray-100"
                  )}
                >
                  <CircleIcon
                    className={cn(
                      "mr-2 h-3 w-3",
                      status === "online"
                        ? "fill-green-600 text-green-600"
                        : "fill-gray-400 text-gray-400"
                    )}
                  />
                  <span
                    className={cn(
                      "capitalize text-muted-foreground",
                      status === "online" ? "text-green-600" : "text-gray-600"
                    )}
                  >
                    {status}
                  </span>
                </Badge>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button type="submit" size="icon">
                      <Save className="h-4 w-4" />
                      <span className="sr-only">Save</span>
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>Save</TooltipContent>
                </Tooltip>
              </div>
            </div>
            <Separator />
            <CollapsibleSection
              node="Input Schema"
              showToggleText={false}
              className="text-md truncate text-start font-medium"
              size="lg"
              iconSize="md"
            >
              <SyntaxHighlighter
                language="json"
                style={atomOneDark}
                wrapLines
                customStyle={{
                  width: "100%",
                  maxWidth: "100%",
                  overflowX: "auto",
                }}
                codeTagProps={{
                  className:
                    "text-xs text-background rounded-lg max-w-full overflow-auto",
                }}
                {...{
                  className:
                    "rounded-lg p-4 overflow-auto max-w-full w-full no-scrollbar",
                }}
              >
                {JSON.stringify(udf.args, null, 2)}
              </SyntaxHighlighter>
            </CollapsibleSection>
            <CollapsibleSection
              node="Response Schema"
              showToggleText={false}
              className="text-md truncate text-start font-medium"
              size="lg"
              iconSize="md"
            >
              <SyntaxHighlighter
                language="json"
                style={atomOneDark}
                wrapLines
                customStyle={{
                  width: "100%",
                  maxWidth: "100%",
                  overflowX: "auto",
                }}
                codeTagProps={{
                  className:
                    "text-xs text-background rounded-lg max-w-full overflow-auto",
                }}
                {...{
                  className:
                    "rounded-lg p-4 overflow-auto max-w-full w-full no-scrollbar",
                }}
              >
                {JSON.stringify(udf.rtype, null, 2)}
              </SyntaxHighlighter>
            </CollapsibleSection>

            <Separator />
            <div className="mb-4 space-y-4">
              <FormField
                control={methods.control}
                name="title"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel className="text-xs">Title</FormLabel>
                    <FormControl>
                      <Input
                        {...field}
                        className="text-xs"
                        placeholder="Add action title..."
                        value={methods.watch("title", "")}
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
                        {...field}
                        className="text-xs"
                        placeholder="Describe your action..."
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <Separator />
              <div className="space-y-4">
                <h4 className="text-m font-medium">Action Inputs</h4>
                <div className="inline-block space-y-2 text-xs text-muted-foreground">
                  <p>{udf?.description}</p>
                </div>
                <div className="space-y-2 text-xs text-muted-foreground">
                  <h5 className="font-bold">Docstring</h5>
                  <p>{udf?.version}</p>
                </div>

                <div className="space-y-2 capitalize">
                  {Object.entries(formConfig).map(([inputKey, fieldConfig]) => (
                    <UDFFormField<Schema>
                      key={inputKey}
                      action={action}
                      inputKey={inputKey}
                      fieldConfig={fieldConfig}
                    />
                  ))}
                </div>
                <CollapsibleSection
                  node="JSON View"
                  showToggleText={false}
                  className="text-md truncate text-start font-medium"
                  size="lg"
                  iconSize="md"
                >
                  <SyntaxHighlighter
                    language="json"
                    style={atomOneDark}
                    wrapLines
                    customStyle={{
                      width: "100%",
                      maxWidth: "100%",
                      overflowX: "auto",
                    }}
                    codeTagProps={{
                      className:
                        "text-xs text-background rounded-lg max-w-full overflow-auto",
                    }}
                    {...{
                      className:
                        "rounded-lg p-4 overflow-auto max-w-full w-full no-scrollbar",
                    }}
                  >
                    {JSON.stringify(methods.watch(), null, 2)}
                  </SyntaxHighlighter>
                </CollapsibleSection>
              </div>
            </div>
          </div>
        </form>
      </FormProvider>
    </div>
  )
}

export function UDFFormField<T extends FieldValues>(props: {
  action: Action
  inputKey: string
  fieldConfig: FieldConfig
}) {
  const { action, inputKey, fieldConfig } = props
  switch (fieldConfig.kind) {
    case "select":
      const defaultVal = action?.inputs?.[inputKey] || fieldConfig.default
      return (
        <UDFFormSelect<T> key={inputKey} defaultValue={defaultVal} {...props} />
      )
    case "textarea":
      return <UDFFormTextarea<T> {...props} />
    case "json":
      return <UDFFormJSON<T> {...props} />
    case "array":
      return <UDFFormArray<T> {...props} />
    case "flatkv":
      return <UDFFormFlatKVArray<T> {...props} />
    default:
      return <UDFFormInputs<T> {...props} />
  }
}

type TDefaultValue<T extends FieldValues> = PathValue<T, FieldPath<T>>

export function UDFFormLabel<T extends FieldValues>({
  inputKey,
  fieldConfig,
}: {
  inputKey: string
  fieldConfig: FieldConfig
}) {
  const { getValues } = useFormContext<T>()
  const typedKey = inputKey as FieldPath<T>
  return (
    <FormLabel className="text-xs">
      <div className="flex items-center space-x-2">
        <span>{undoSlugify(inputKey)}</span>
        {fieldConfig.optional && (
          <span className="text-muted-foreground"> (Optional)</span>
        )}
        {fieldConfig.copyable && (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                type="button"
                variant="ghost"
                className="m-0 h-4 w-4 p-0"
                onClick={() => {
                  copyToClipboard({
                    value: getValues(typedKey),
                    message: "Copied URL to clipboard",
                  })
                  toast({
                    title: "Copied to clipboard",
                    description: `Copied ${typedKey} to clipboard.`,
                  })
                }}
              >
                <CopyIcon className="h-3 w-3" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>Copy</TooltipContent>
          </Tooltip>
        )}
      </div>
    </FormLabel>
  )
}
interface UDFFormFieldProps<T extends FieldValues> {
  inputKey: string
  fieldConfig: FieldConfig
  defaultValue?: T[keyof T]
}

export function UDFFormTextarea<T extends FieldValues>({
  inputKey,
  fieldConfig,
  defaultValue,
}: UDFFormFieldProps<T>) {
  const { control, watch } = useFormContext<T>()
  if (fieldConfig.kind !== "textarea") {
    throw new Error(
      `UDFFormTextarea received invalid input type ${fieldConfig.kind}`
    )
  }
  const typedKey = inputKey as FieldPath<T>
  return (
    <FormField
      key={inputKey}
      control={control}
      name={typedKey}
      render={({ field }) => (
        <FormItem>
          <UDFFormLabel inputKey={inputKey} fieldConfig={fieldConfig} />
          <FormControl>
            <Textarea
              {...field}
              value={watch(typedKey, defaultValue)}
              className="min-h-48 text-xs"
              placeholder={fieldConfig.placeholder ?? "Input text here..."}
            />
          </FormControl>
          <FormMessage />
        </FormItem>
      )}
    />
  )
}

export function UDFFormSelect<T extends FieldValues>({
  inputKey,
  fieldConfig,
  defaultValue,
}: UDFFormFieldProps<T>) {
  const { control, watch, setValue } = useFormContext<T>()
  if (fieldConfig.kind !== "select") {
    throw new Error(
      `UDFFormSelect received invalid input type ${fieldConfig.kind}`
    )
  }
  // Set the default value in the form

  const typedKey = inputKey as FieldPath<T>
  const typedDefault = defaultValue as PathValue<T, FieldPath<T>>
  const parser = getParser(fieldConfig)
  return (
    <FormField
      key={inputKey}
      control={control}
      name={typedKey}
      render={({ field }) => (
        <FormItem>
          <UDFFormLabel inputKey={inputKey} fieldConfig={fieldConfig} />
          <FormControl>
            <Select
              value={String(watch(typedKey, typedDefault))} // Ensure the Select component uses the current field value
              defaultValue={typedDefault} // Set the default value from the fetched action data
              onValueChange={(value: string) => {
                field.onChange(parser(value))
              }}
            >
              <SelectTrigger>
                <SelectValue placeholder="Select a value..." />
              </SelectTrigger>
              <SelectContent>
                {fieldConfig.options?.map((option: string) => (
                  <SelectItem key={option} value={option}>
                    {option}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </FormControl>
          <FormMessage />
        </FormItem>
      )}
    />
  )
}

/**
 *
 * @param param0
 * @returns JSON Form Field
 */
export function UDFFormJSON<T extends FieldValues>({
  inputKey,
  fieldConfig,
  defaultValue,
}: UDFFormFieldProps<T>) {
  const { control, watch } = useFormContext<T>()
  if (fieldConfig.kind !== "json") {
    throw new Error(
      `UDFFormJSON received invalid input type ${fieldConfig.kind}`
    )
  }
  const typedKey = inputKey as FieldPath<T>
  return (
    <FormField
      key={inputKey}
      control={control}
      name={typedKey}
      render={({ field }) => (
        <FormItem>
          <UDFFormLabel inputKey={inputKey} fieldConfig={fieldConfig} />
          <FormControl>
            <pre>
              <Textarea
                {...field}
                value={watch(typedKey, defaultValue)}
                className="min-h-48 text-xs"
                placeholder={fieldConfig.placeholder ?? "Input JSON here..."}
              />
            </pre>
          </FormControl>
          <FormMessage />
        </FormItem>
      )}
    />
  )
}

export function UDFFormArray<T extends FieldValues>({
  inputKey,
  fieldConfig,
  defaultValue,
}: UDFFormFieldProps<T>) {
  const { control, watch, register } = useFormContext<T>()
  const typedKey = inputKey as FieldPath<T>
  const { fields, append, remove } = useFieldArray<T>({
    control,
    name: inputKey as ArrayPath<T>,
  })
  if (fieldConfig.kind !== "array") {
    throw new Error(
      `UDFFormArray received invalid input type ${fieldConfig.kind}`
    )
  }
  return (
    <FormField
      key={inputKey}
      control={control}
      name={typedKey}
      render={() => (
        <FormItem>
          <UDFFormLabel inputKey={inputKey} fieldConfig={fieldConfig} />
          <div className="flex flex-col space-y-2">
            {fields.map((field, index) => {
              return (
                <div
                  key={`${field.id}.${index}`}
                  className="flex w-full items-center gap-2"
                >
                  <FormControl>
                    <Input
                      className="text-xs"
                      key={`${field.id}.${index}`}
                      {...register(
                        // @ts-ignore
                        `${inputKey}.${index}` as const
                      )}
                      value={watch(
                        // @ts-ignore
                        `${inputKey}.${index}` as const,
                        ""
                      )}
                    />
                  </FormControl>

                  <Button
                    type="button"
                    variant="outline"
                    size="icon"
                    className={DELETE_BUTTON_STYLE}
                    onClick={() => remove(index)}
                  >
                    <Trash2Icon className="size-4" />
                  </Button>
                </div>
              )
            })}
            <Button
              type="button"
              onClick={() => append(defaultValue as TDefaultValue<T>)}
            >
              Add Item
            </Button>
          </div>
          <FormMessage />
        </FormItem>
      )}
    />
  )
}

export function UDFFormInputs<T extends FieldValues>({
  inputKey,
  fieldConfig,
  defaultValue,
}: UDFFormFieldProps<T>) {
  const { control, watch } = useFormContext<T>()
  if (fieldConfig.kind !== "input") {
    throw new Error(
      `UDFFormInputs received invalid input type ${fieldConfig.kind}`
    )
  }
  const typedKey = inputKey as FieldPath<T>
  const parser = getParser(fieldConfig)
  return (
    <FormField
      key={inputKey}
      control={control}
      name={typedKey}
      render={({ field }) => (
        <FormItem>
          <UDFFormLabel inputKey={inputKey} fieldConfig={fieldConfig} />
          <FormControl>
            <Input
              {...field}
              value={watch(typedKey, defaultValue)}
              defaultValue={defaultValue as PathValue<T, FieldPath<T>>}
              className="text-xs"
              placeholder={fieldConfig.placeholder ?? "Input text here..."}
              disabled={fieldConfig.disabled}
              onChange={(e) => {
                const parsedValue = parser(e.target.value)
                field.onChange(parsedValue)
              }}
            />
          </FormControl>
          <FormMessage />
        </FormItem>
      )}
    />
  )
}

interface UDFFormKVArrayProps<T extends FieldValues>
  extends UDFFormFieldProps<T> {
  keyName?: string
  valueName?: string
  isPassword?: boolean
}

export function UDFFormFlatKVArray<T extends FieldValues>({
  inputKey,
  fieldConfig,
  defaultValue,
  // Extra
  isPassword = false,
}: UDFFormKVArrayProps<T>) {
  const { control, register } = useFormContext<T>()
  const typedKey = inputKey as FieldPath<T>
  const { fields, append, remove } = useFieldArray<T>({
    control,
    name: inputKey as ArrayPath<T>,
  })
  if (fieldConfig.kind !== "flatkv") {
    throw new Error(
      `UDFFormFlatKVArray received invalid input type ${fieldConfig.kind}`
    )
  }
  const valueProps = isPassword ? { type: "password" } : {}

  const keyName = fieldConfig.keyPlaceholder ?? "key"
  const valueName = fieldConfig.valuePlaceholder ?? "value"

  return (
    <FormField
      key={inputKey}
      control={control}
      name={typedKey}
      render={() => (
        <FormItem>
          <UDFFormLabel inputKey={inputKey} fieldConfig={fieldConfig} />
          <div className="flex flex-col space-y-2">
            {fields.map((field, index) => {
              return (
                <div
                  key={`${field.id}.${index}`}
                  className="flex w-full items-center gap-2"
                >
                  <FormControl>
                    <Input
                      id={`key-${index}`}
                      className="text-sm"
                      // @ts-ignore
                      {...register(`${inputKey}.${index}.${keyName}` as const, {
                        required: true,
                      })}
                      placeholder={keyName}
                    />
                  </FormControl>
                  <FormControl>
                    <Input
                      id={`value-${index}`}
                      className="text-sm"
                      {...register(
                        // @ts-ignore
                        `${inputKey}.${index}.${valueName}` as const,
                        {
                          required: true,
                        }
                      )}
                      placeholder={valueName}
                      {...valueProps}
                    />
                  </FormControl>

                  <Button
                    type="button"
                    variant="ghost"
                    className={DELETE_BUTTON_STYLE}
                    onClick={() => remove(index)}
                    // If this field is optional, enable the delete button
                    disabled={!fieldConfig.optional && fields.length === 1}
                  >
                    <Trash2Icon className="size-3.5" />
                  </Button>
                </div>
              )
            })}
            <Button
              type="button"
              variant="outline"
              onClick={() => append(defaultValue as TDefaultValue<T>)}
              className="space-x-2 text-xs"
            >
              <PlusCircle className="mr-2 h-4 w-4" />
              Add Item
            </Button>
          </div>
          <FormMessage />
        </FormItem>
      )}
    />
  )
}

///////////////////////////
// Methods
///////////////////////////

/**
 * Parse the input string value into the correct type.
 * We need this to ensure that the value is correctly coerced before.
 * @param fieldConfig
 * @returns
 */
function getParser(fieldConfig: FieldConfig): (value: string) => any {
  switch (fieldConfig.dtype) {
    case "number":
      return (value: string) => parseFloat(value)
    case "integer":
      return (value: string) => parseInt(value, 10)
    case "boolean":
      return (value: string) => value === "true"
    default:
      return (value: string) => value
  }
}

export function populateForm(action?: Action): {
  title: string
  description: string
  [key: string]: any
} {
  return {
    title: action?.title ?? "",
    description: action?.description ?? "",
    ...(action?.inputs ? processInputs(action.inputs) : {}), // Unpack the inputs object
  }
}

export function processInputs(
  inputs: Record<string, any>
): Record<string, any> {
  return Object.entries(inputs).reduce(
    (newInputs: Record<string, any>, [key, value]) => {
      if (value === null || value === undefined) {
        // Is null or undefined
        newInputs[key] = ""
      } else if (
        // Is a serializable object (not an array or date)
        typeof value === "object" &&
        value !== null &&
        !Array.isArray(value) &&
        !(value instanceof Date)
      ) {
        newInputs[key] = JSON.stringify(value) // Stringify object values
      } else {
        // Includes arrays
        newInputs[key] = value // Keep non-object values as is
      }
      return newInputs
    },
    {}
  )
}
