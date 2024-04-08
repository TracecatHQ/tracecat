import React from "react"
import { CopyIcon } from "@radix-ui/react-icons"
import { DeleteIcon } from "lucide-react"
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
interface ActionFormFieldProps {
  inputKey: string
  inputOption: ActionFieldOption
  defaultValue?: string
}

export function ActionFormTextarea<T extends FieldValues>({
  inputKey,
  inputOption,
  defaultValue = "",
}: ActionFormFieldProps) {
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
              value={watch(typedKey, defaultValue as TDefaultValue<T>)}
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
  defaultValue = "",
}: ActionFormFieldProps) {
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
              value={String(watch(typedKey, defaultValue as TDefaultValue<T>))} // Ensure the Select component uses the current field value
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
  defaultValue = "",
}: ActionFormFieldProps) {
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
                value={watch(typedKey, defaultValue as TDefaultValue<T>)}
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
  defaultValue = "",
}: ActionFormFieldProps) {
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
                    variant="default"
                    className="bg-red-400 p-0 px-3"
                    onClick={() => remove(index)}
                  >
                    <DeleteIcon className="stroke-8 h-4 w-4 stroke-white/80" />
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
  defaultValue = "",
}: ActionFormFieldProps) {
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
              type={inputOption.inputType}
              {...field}
              value={watch(typedKey, defaultValue as TDefaultValue<T>)}
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
