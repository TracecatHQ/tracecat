import React from "react"
import { CopyIcon } from "@radix-ui/react-icons"
import { PlusCircle, Trash2Icon } from "lucide-react"
import {
  ArrayPath,
  FieldPath,
  FieldPathValue,
  FieldValues,
  PathValue,
  useFieldArray,
  useFormContext,
} from "react-hook-form"

import { copyToClipboard, undoSlugify } from "@/lib/utils"
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
import { Textarea } from "@/components/ui/textarea"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import { ActionFieldOption } from "@/components/workspace/panel/action/schemas"
import { DELETE_BUTTON_STYLE } from "@/styles/tailwind"

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

type TDefaultValue<T extends FieldValues> = PathValue<T, FieldPath<T>>
type TValue<T extends FieldValues> = FieldPathValue<T, FieldPath<T>>

export function ActionFormLabel<T extends FieldValues>({
  inputKey,
  inputOption,
}: {
  inputKey: string
  inputOption: ActionFieldOption
}) {
  const { getValues } = useFormContext<T>()
  const typedKey = inputKey as FieldPath<T>
  return (
    <FormLabel className="text-xs">
      <div className="flex items-center space-x-2">
        <span>{undoSlugify(inputKey)}</span>
        {inputOption.optional && (
          <span className="text-muted-foreground"> (Optional)</span>
        )}
        {inputOption.copyable && (
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
interface ActionFormFieldProps<T extends FieldValues> {
  inputKey: string
  inputOption: ActionFieldOption
  defaultValue?: T[keyof T]
}

export function ActionFormTextarea<T extends FieldValues>({
  inputKey,
  inputOption,
  defaultValue,
}: ActionFormFieldProps<T>) {
  const { control, watch } = useFormContext<T>()
  const typedKey = inputKey as FieldPath<T>
  return (
    <FormField
      key={inputKey}
      control={control}
      name={typedKey}
      render={({ field }) => (
        <FormItem>
          <ActionFormLabel inputKey={inputKey} inputOption={inputOption} />
          <FormControl>
            <Textarea
              {...field}
              value={watch(typedKey, defaultValue)}
              className="min-h-48 text-xs"
              placeholder={inputOption.placeholder ?? "Input text here..."}
            />
          </FormControl>
          <FormMessage />
        </FormItem>
      )}
    />
  )
}

export function ActionFormSelect<T extends FieldValues>({
  inputKey,
  inputOption,
  defaultValue,
}: ActionFormFieldProps<T>) {
  const { control, watch } = useFormContext<T>()
  const typedKey = inputKey as FieldPath<T>
  const parser = getParser(inputOption)
  return (
    <FormField
      key={inputKey}
      control={control}
      name={typedKey}
      render={({ field }) => (
        <FormItem>
          <ActionFormLabel inputKey={inputKey} inputOption={inputOption} />
          <FormControl>
            <Select
              value={String(watch(typedKey, defaultValue))} // Ensure the Select component uses the current field value
              defaultValue={defaultValue} // Set the default value from the fetched action data
              onValueChange={(value: string) => {
                field.onChange(parser(value))
              }}
            >
              <SelectTrigger>
                <SelectValue placeholder="Select a value..." />
              </SelectTrigger>
              <SelectContent>
                {inputOption.options?.map((option: string) => (
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

export function ActionFormJSON<T extends FieldValues>({
  inputKey,
  inputOption,
  defaultValue,
}: ActionFormFieldProps<T>) {
  const { control, watch } = useFormContext<T>()
  const typedKey = inputKey as FieldPath<T>
  return (
    <FormField
      key={inputKey}
      control={control}
      name={typedKey}
      render={({ field }) => (
        <FormItem>
          <ActionFormLabel inputKey={inputKey} inputOption={inputOption} />
          <FormControl>
            <pre>
              <Textarea
                {...field}
                value={watch(typedKey, defaultValue)}
                className="min-h-48 text-xs"
                placeholder={inputOption.placeholder ?? "Input JSON here..."}
              />
            </pre>
          </FormControl>
          <FormMessage />
        </FormItem>
      )}
    />
  )
}

export function ActionFormArray<T extends FieldValues>({
  inputKey,
  inputOption,
  defaultValue,
}: ActionFormFieldProps<T>) {
  const { control, watch, register } = useFormContext<T>()
  const typedKey = inputKey as FieldPath<T>

  const { fields, append, remove } = useFieldArray<T>({
    control,
    name: inputKey as ArrayPath<T>,
  })
  return (
    <FormField
      key={inputKey}
      control={control}
      name={typedKey}
      render={({ field }) => (
        <FormItem>
          <ActionFormLabel inputKey={inputKey} inputOption={inputOption} />
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

export function ActionFormInputs<T extends FieldValues>({
  inputKey,
  inputOption,
  defaultValue,
}: ActionFormFieldProps<T>) {
  const { control, watch } = useFormContext<T>()
  const typedKey = inputKey as FieldPath<T>
  const parser = getParser(inputOption)
  return (
    <FormField
      key={inputKey}
      control={control}
      name={typedKey}
      render={({ field }) => (
        <FormItem>
          <ActionFormLabel inputKey={inputKey} inputOption={inputOption} />
          <FormControl>
            <Input
              {...field}
              value={watch(typedKey, defaultValue)}
              className="text-xs"
              placeholder={inputOption.placeholder ?? "Input text here..."}
              disabled={inputOption.disabled}
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

function getParser(inputOption: ActionFieldOption): (value: string) => any {
  switch (inputOption.dtype) {
    case "float":
      return (value: string) => parseFloat(value)
    case "int":
      return (value: string) => parseInt(value, 10)
    case "bool":
      return (value: string) => value === "true"
    default:
      return (value: string) => value
  }
}

interface ActionFormKVArrayProps<T extends FieldValues>
  extends ActionFormFieldProps<T> {
  keyName?: string
  valueName?: string
  isPassword?: boolean
}

export function ActionFormFlatKVArray<T extends FieldValues>({
  inputKey,
  inputOption,
  defaultValue,
  // Extra
  isPassword = false,
}: ActionFormKVArrayProps<T>) {
  const { control, register } = useFormContext<T>()
  const typedKey = inputKey as FieldPath<T>
  const { fields, append, remove } = useFieldArray<T>({
    control,
    name: inputKey as ArrayPath<T>,
  })
  const valueProps = isPassword ? { type: "password" } : {}

  const keyName = inputOption.key ?? "key"
  const valueName = inputOption.value ?? "value"

  return (
    <FormField
      key={inputKey}
      control={control}
      name={typedKey}
      render={({ field }) => (
        <FormItem>
          <ActionFormLabel inputKey={inputKey} inputOption={inputOption} />
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
                    disabled={!inputOption.optional && fields.length === 1}
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
