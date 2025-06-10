import React from "react"
import { Code } from "@/client"
import { useWorkflow } from "@/providers/workflow"
import { useWorkspace } from "@/providers/workspace"
import { PlusCircleIcon, PlusIcon } from "lucide-react"
import { useFormContext } from "react-hook-form"

import { TracecatJsonSchema } from "@/lib/schema"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
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
import { CodeMirrorEditor } from "@/components/editor/codemirror"
import { DynamicCustomEditor } from "@/components/editor/dynamic"
import { CustomTagInput } from "@/components/tags-input"

export interface FormComponentProps {
  label: string
  fieldName: string
  fieldDefn: TracecatJsonSchema
  description?: string
  className?: string
}

export function formatInlineCode(text: string) {
  return text.split(/(`[^`]+`)/).map((part, i) => {
    if (part.startsWith("`") && part.endsWith("`")) {
      return (
        <code
          key={i}
          className="rounded bg-muted px-1 py-0.5 font-mono tracking-tight"
        >
          {part.slice(1, -1)}
        </code>
      )
    }
    return part
  })
}

export function FormLabelComponent({
  label,
  description,
}: {
  label: string
  description?: string
}) {
  return (
    <FormLabel className="flex flex-col gap-1 text-xs font-medium">
      <span className="font-semibold capitalize">{label}</span>
      {description && (
        <span className="text-xs text-muted-foreground">
          {formatInlineCode(description)}
        </span>
      )}
    </FormLabel>
  )
}

export function YamlField({
  label,
  fieldName,
  description,
}: {
  label: string
  fieldName: string
  description?: string
}) {
  const { workspace } = useWorkspace()
  const { workflow } = useWorkflow()
  const methods = useFormContext()
  return (
    <FormField
      name={`inputs.${fieldName}`}
      control={methods.control}
      render={({ field }) => (
        <FormItem>
          <FormLabelComponent label={label} description={description} />
          {/* Place form message above because it's not visible otherwise */}
          <FormMessage className="whitespace-pre-line" />
          <FormControl>
            <DynamicCustomEditor
              className="w-full"
              value={field.value}
              onChange={field.onChange}
              defaultLanguage="yaml-extended"
              workspaceId={workspace?.id}
              workflowId={workflow?.id}
            />
          </FormControl>
        </FormItem>
      )}
    />
  )
}

export function CodeMirrorCodeField({
  label,
  fieldName,
  fieldDefn,
  code,
  className,
}: FormComponentProps & {
  code: Code
}) {
  const methods = useFormContext()
  const { description } = fieldDefn
  return (
    <FormField
      name={`inputs.${fieldName}`}
      control={methods.control}
      render={({ field }) => {
        return (
          <FormItem>
            <FormLabelComponent label={label} description={description} />
            <FormMessage className="whitespace-pre-line" />
            <FormControl>
              <CodeMirrorEditor
                value={field.value}
                onChange={field.onChange}
                language={code.lang || "python"}
                readOnly={false}
                className={className}
              />
            </FormControl>
          </FormItem>
        )
      }}
    />
  )
}

export function TextField({ label, fieldName, fieldDefn }: FormComponentProps) {
  const methods = useFormContext()
  const { description } = fieldDefn
  return (
    <FormField
      name={`inputs.${fieldName}`}
      control={methods.control}
      render={({ field }) => (
        <FormItem>
          <FormLabelComponent label={label} description={description} />
          <FormMessage className="whitespace-pre-line" />
          <FormControl>
            <Input type="text" value={field.value} onChange={field.onChange} />
          </FormControl>
        </FormItem>
      )}
    />
  )
}

export function SelectField({
  label,
  fieldName,
  fieldDefn,
  options,
  multiple,
}: FormComponentProps & {
  options: string[]
  multiple?: boolean
}) {
  const methods = useFormContext()
  const { description } = fieldDefn
  return (
    <FormField
      name={`inputs.${fieldName}`}
      control={methods.control}
      render={({ field }) => (
        <FormItem>
          <FormLabelComponent label={label} description={description} />
          <FormMessage className="whitespace-pre-line" />
          <FormControl>
            <Select
              value={field.value}
              onValueChange={field.onChange}
              disabled={multiple}
            >
              <SelectTrigger className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50">
                <SelectValue placeholder="Select an option" />
              </SelectTrigger>
              <SelectContent>
                {options.map((option) => (
                  <SelectItem key={option} value={option}>
                    {option}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </FormControl>
        </FormItem>
      )}
    />
  )
}

export function TagInputField({
  label,
  fieldName,
  fieldDefn,
}: FormComponentProps) {
  const methods = useFormContext()
  const { description } = fieldDefn
  return (
    <FormField
      name={`inputs.${fieldName}`}
      control={methods.control}
      render={({ field }) => (
        <FormItem>
          <FormLabelComponent label={label} description={description} />
          <FormMessage className="whitespace-pre-line" />
          <FormControl>
            <CustomTagInput
              {...field}
              tags={field.value || []}
              setTags={field.onChange}
              placeholder="Add values..."
            />
          </FormControl>
        </FormItem>
      )}
    />
  )
}

export function TextAreaField({
  label,
  fieldName,
  fieldDefn,
  rows,
  placeholder,
}: FormComponentProps & {
  rows?: number
  placeholder?: string
}) {
  const methods = useFormContext()
  const { description } = fieldDefn
  return (
    <FormField
      name={`inputs.${fieldName}`}
      control={methods.control}
      render={({ field }) => (
        <FormItem>
          <FormLabelComponent label={label} description={description} />
          <FormMessage className="whitespace-pre-line" />
          <FormControl>
            <Textarea
              rows={rows}
              placeholder={placeholder}
              value={field.value}
              onChange={field.onChange}
            />
          </FormControl>
        </FormItem>
      )}
    />
  )
}

export function SliderField({
  label,
  fieldName,
  fieldDefn,
  minVal,
  maxVal,
  step,
}: FormComponentProps & {
  minVal?: number
  maxVal?: number
  step?: number
}) {
  const methods = useFormContext()
  const { description } = fieldDefn
  return (
    <FormField
      name={`inputs.${fieldName}`}
      control={methods.control}
      render={({ field }) => (
        <FormItem>
          <FormLabelComponent label={label} description={description} />
          <FormMessage className="whitespace-pre-line" />
          <FormControl>
            <Input
              type="range"
              min={minVal}
              max={maxVal}
              step={step}
              value={field.value}
              onChange={field.onChange}
            />
          </FormControl>
        </FormItem>
      )}
    />
  )
}

export function IntegerField({
  label,
  fieldName,
  fieldDefn,
  minVal,
  maxVal,
  step,
}: FormComponentProps & {
  minVal?: number
  maxVal?: number
  step?: number
}) {
  const methods = useFormContext()
  const { description } = fieldDefn
  return (
    <FormField
      name={`inputs.${fieldName}`}
      control={methods.control}
      render={({ field }) => (
        <FormItem>
          <FormLabelComponent label={label} description={description} />
          <FormMessage className="whitespace-pre-line" />
          <FormControl>
            <Input
              type="number"
              value={field.value}
              min={minVal}
              max={maxVal}
              step={step}
              onChange={field.onChange}
            />
          </FormControl>
        </FormItem>
      )}
    />
  )
}

export function FloatField({
  label,
  fieldName,
  fieldDefn,
  minVal,
  maxVal,
  step,
}: FormComponentProps & {
  minVal?: number
  maxVal?: number
  step?: number
}) {
  const methods = useFormContext()
  const { description } = fieldDefn
  return (
    <FormField
      name={`inputs.${fieldName}`}
      control={methods.control}
      render={({ field }) => (
        <FormItem>
          <FormLabelComponent label={label} description={description} />
          <FormMessage className="whitespace-pre-line" />
          <FormControl>
            <Input
              type="number"
              min={minVal}
              max={maxVal}
              step={step}
              value={field.value}
              onChange={field.onChange}
            />
          </FormControl>
        </FormItem>
      )}
    />
  )
}

export function ToggleField({
  label,
  fieldName,
  fieldDefn,
  labelOn = "True",
  labelOff = "False",
}: FormComponentProps & {
  labelOn?: string
  labelOff?: string
}) {
  const methods = useFormContext()
  const { description } = fieldDefn
  return (
    <FormField
      name={`inputs.${fieldName}`}
      control={methods.control}
      render={({ field }) => (
        <FormItem>
          <FormLabelComponent label={label} description={description} />
          <FormMessage className="whitespace-pre-line" />
          <FormControl>
            <div className="flex items-center space-x-2">
              <Checkbox
                checked={field.value}
                onCheckedChange={field.onChange}
              />
              <span className="text-sm text-muted-foreground">
                {field.value ? labelOn : labelOff}
              </span>
            </div>
          </FormControl>
        </FormItem>
      )}
    />
  )
}

export function KeyValueField({
  label,
  fieldName,
  fieldDefn,
}: FormComponentProps) {
  const methods = useFormContext()
  const { description } = fieldDefn

  return (
    <FormField
      name={`inputs.${fieldName}`}
      control={methods.control}
      render={({ field }) => {
        const keyValuePairs = (field.value as Record<string, string>) || {}
        const pairs = Object.entries(keyValuePairs)

        const addPair = () => {
          const newPairs = { ...keyValuePairs, "": "" }
          field.onChange(newPairs)
        }

        const updateKey = (oldKey: string, newKey: string) => {
          const newPairs = { ...keyValuePairs }
          if (oldKey !== newKey) {
            delete newPairs[oldKey]
            newPairs[newKey] = keyValuePairs[oldKey] || ""
          }
          field.onChange(newPairs)
        }

        const updateValue = (key: string, newValue: string) => {
          const newPairs = { ...keyValuePairs, [key]: newValue }
          field.onChange(newPairs)
        }

        const removePair = (keyToRemove: string) => {
          const newPairs = { ...keyValuePairs }
          delete newPairs[keyToRemove]
          field.onChange(newPairs)
        }

        return (
          <FormItem>
            <FormLabelComponent label={label} description={description} />
            <FormMessage className="whitespace-pre-line" />
            <FormControl>
              <div className="space-y-2">
                {pairs.map(([key, value], index) => (
                  <div key={index} className="flex items-center gap-2">
                    <Input
                      type="text"
                      placeholder="Key"
                      value={key}
                      onChange={(e) => updateKey(key, e.target.value)}
                      className="flex-1"
                    />
                    <Input
                      type="text"
                      placeholder="Value"
                      value={value as string}
                      onChange={(e) => updateValue(key, e.target.value)}
                      className="flex-1"
                    />
                    <Button
                      variant="ghost"
                      onClick={() => removePair(key)}
                      className="px-2 text-red-500 hover:text-red-700"
                      aria-label="Remove pair"
                    >
                      ×
                    </Button>
                  </div>
                ))}
                <Button
                  variant="outline"
                  onClick={addPair}
                  className="h-7 w-full rounded-md border border-dashed border-gray-300 px-3 py-2 text-xs text-muted-foreground transition-colors hover:border-gray-400 hover:bg-gray-50"
                >
                  <PlusCircleIcon className="mr-1 size-3" />
                  Add key-value pair
                </Button>
              </div>
            </FormControl>
          </FormItem>
        )
      }}
    />
  )
}
