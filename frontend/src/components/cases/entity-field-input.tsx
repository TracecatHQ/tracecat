"use client"

import { Plus, X } from "lucide-react"
import { type Control, useFieldArray } from "react-hook-form"
import type { EntitySchemaField } from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  FormControl,
  FormDescription,
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

interface EntityFieldInputProps {
  field: EntitySchemaField
  control: Control<Record<string, unknown>>
  name: string
  disabled?: boolean
}

export function EntityFieldInput({
  field,
  control,
  name,
  disabled = false,
}: EntityFieldInputProps) {
  const fieldType = field.type.toUpperCase()

  // Handle basic text fields
  if (fieldType === "TEXT" || fieldType === "STRING") {
    return (
      <FormField
        control={control}
        name={name}
        render={({ field: formField }) => (
          <FormItem>
            <FormLabel className="text-xs">{field.display_name}</FormLabel>
            {field.description && (
              <FormDescription className="text-xs">
                {field.description}
              </FormDescription>
            )}
            <FormControl>
              <Input
                {...formField}
                value={formField.value as string}
                disabled={disabled}
                placeholder={`Enter ${field.display_name.toLowerCase()}`}
                className="h-8 text-xs"
              />
            </FormControl>
            <FormMessage className="text-xs" />
          </FormItem>
        )}
      />
    )
  }

  // Handle long text fields
  if (fieldType === "LONGTEXT" || fieldType === "TEXTAREA") {
    return (
      <FormField
        control={control}
        name={name}
        render={({ field: formField }) => (
          <FormItem>
            <FormLabel className="text-xs">{field.display_name}</FormLabel>
            {field.description && (
              <FormDescription className="text-xs">
                {field.description}
              </FormDescription>
            )}
            <FormControl>
              <Textarea
                {...formField}
                value={(formField.value as string) || ""}
                disabled={disabled}
                placeholder={`Enter ${field.display_name.toLowerCase()}`}
                className="min-h-[80px] text-xs"
              />
            </FormControl>
            <FormMessage className="text-xs" />
          </FormItem>
        )}
      />
    )
  }

  // Handle integer fields
  if (fieldType === "INTEGER" || fieldType === "INT") {
    return (
      <FormField
        control={control}
        name={name}
        render={({ field: formField }) => (
          <FormItem>
            <FormLabel className="text-xs">{field.display_name}</FormLabel>
            {field.description && (
              <FormDescription className="text-xs">
                {field.description}
              </FormDescription>
            )}
            <FormControl>
              <Input
                {...formField}
                value={formField.value as string}
                type="number"
                disabled={disabled}
                placeholder="0"
                className="h-8 text-xs"
                onChange={(e) => {
                  const value = e.target.value
                  formField.onChange(value ? parseInt(value, 10) : null)
                }}
              />
            </FormControl>
            <FormMessage className="text-xs" />
          </FormItem>
        )}
      />
    )
  }

  // Handle number/float fields
  if (
    fieldType === "NUMBER" ||
    fieldType === "FLOAT" ||
    fieldType === "DECIMAL"
  ) {
    return (
      <FormField
        control={control}
        name={name}
        render={({ field: formField }) => (
          <FormItem>
            <FormLabel className="text-xs">{field.display_name}</FormLabel>
            {field.description && (
              <FormDescription className="text-xs">
                {field.description}
              </FormDescription>
            )}
            <FormControl>
              <Input
                {...formField}
                value={formField.value as string}
                type="number"
                step="any"
                disabled={disabled}
                placeholder="0.0"
                className="h-8 text-xs"
                onChange={(e) => {
                  const value = e.target.value
                  formField.onChange(value ? parseFloat(value) : null)
                }}
              />
            </FormControl>
            <FormMessage className="text-xs" />
          </FormItem>
        )}
      />
    )
  }

  // Handle boolean fields
  if (fieldType === "BOOL" || fieldType === "BOOLEAN") {
    return (
      <FormField
        control={control}
        name={name}
        render={({ field: formField }) => (
          <FormItem className="flex flex-row items-start space-x-3 space-y-0">
            <FormControl>
              <Checkbox
                checked={!!formField.value}
                onCheckedChange={formField.onChange}
                disabled={disabled}
              />
            </FormControl>
            <div className="space-y-1 leading-none">
              <FormLabel className="text-xs">{field.display_name}</FormLabel>
              {field.description && (
                <FormDescription className="text-xs">
                  {field.description}
                </FormDescription>
              )}
            </div>
            <FormMessage className="text-xs" />
          </FormItem>
        )}
      />
    )
  }

  // Handle date fields
  if (fieldType === "DATE") {
    return (
      <FormField
        control={control}
        name={name}
        render={({ field: formField }) => (
          <FormItem>
            <FormLabel className="text-xs">{field.display_name}</FormLabel>
            {field.description && (
              <FormDescription className="text-xs">
                {field.description}
              </FormDescription>
            )}
            <FormControl>
              <Input
                {...formField}
                value={(formField.value as string) || ""}
                type="date"
                disabled={disabled}
                className="h-8 text-xs"
              />
            </FormControl>
            <FormMessage className="text-xs" />
          </FormItem>
        )}
      />
    )
  }

  // Handle datetime fields
  if (fieldType === "DATETIME" || fieldType === "TIMESTAMP") {
    return (
      <FormField
        control={control}
        name={name}
        render={({ field: formField }) => (
          <FormItem>
            <FormLabel className="text-xs">{field.display_name}</FormLabel>
            {field.description && (
              <FormDescription className="text-xs">
                {field.description}
              </FormDescription>
            )}
            <FormControl>
              <Input
                {...formField}
                value={(formField.value as string) || ""}
                type="datetime-local"
                disabled={disabled}
                className="h-8 text-xs"
              />
            </FormControl>
            <FormMessage className="text-xs" />
          </FormItem>
        )}
      />
    )
  }

  // Handle select fields
  if (fieldType === "SELECT" || fieldType === "ENUM") {
    return (
      <FormField
        control={control}
        name={name}
        render={({ field: formField }) => (
          <FormItem>
            <FormLabel className="text-xs">{field.display_name}</FormLabel>
            {field.description && (
              <FormDescription className="text-xs">
                {field.description}
              </FormDescription>
            )}
            <Select
              onValueChange={formField.onChange}
              value={(formField.value as string) || ""}
              disabled={disabled}
            >
              <FormControl>
                <SelectTrigger className="h-8 text-xs">
                  <SelectValue placeholder="Select an option" />
                </SelectTrigger>
              </FormControl>
              <SelectContent>
                {field.enum_options?.map((option) => (
                  <SelectItem key={option} value={option} className="text-xs">
                    {option}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <FormMessage className="text-xs" />
          </FormItem>
        )}
      />
    )
  }

  // Handle multi-select fields
  if (fieldType === "MULTI_SELECT" || fieldType === "MULTISELECT") {
    return (
      <FormField
        control={control}
        name={name}
        render={({ field: formField }) => (
          <FormItem>
            <FormLabel className="text-xs">{field.display_name}</FormLabel>
            {field.description && (
              <FormDescription className="text-xs">
                {field.description}
              </FormDescription>
            )}
            <div className="space-y-2">
              <Select
                onValueChange={(value) => {
                  const currentValues = (formField.value as string[]) || []
                  if (!currentValues.includes(value)) {
                    formField.onChange([...currentValues, value])
                  }
                }}
                disabled={disabled}
              >
                <SelectTrigger className="h-8 text-xs">
                  <SelectValue placeholder="Add an option" />
                </SelectTrigger>
                <SelectContent>
                  {field.enum_options
                    ?.filter(
                      (option) =>
                        !((formField.value as string[]) || []).includes(option)
                    )
                    .map((option) => (
                      <SelectItem
                        key={option}
                        value={option}
                        className="text-xs"
                      >
                        {option}
                      </SelectItem>
                    ))}
                </SelectContent>
              </Select>
              <div className="flex flex-wrap gap-1">
                {((formField.value as string[]) || []).map((value: string) => (
                  <Badge
                    key={value}
                    variant="secondary"
                    className="text-xs h-6 pr-1"
                  >
                    {value}
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="h-4 w-4 p-0 ml-1"
                      onClick={() => {
                        formField.onChange(
                          ((formField.value as string[]) || []).filter(
                            (v: string) => v !== value
                          )
                        )
                      }}
                      disabled={disabled}
                    >
                      <X className="h-3 w-3" />
                    </Button>
                  </Badge>
                ))}
              </div>
            </div>
            <FormMessage className="text-xs" />
          </FormItem>
        )}
      />
    )
  }

  // Handle array fields
  if (fieldType.startsWith("ARRAY_")) {
    const itemType = fieldType.replace("ARRAY_", "")
    return (
      <ArrayFieldInput
        field={field}
        control={control}
        name={name}
        itemType={itemType}
        disabled={disabled}
      />
    )
  }

  // Handle JSON fields
  if (fieldType === "JSON" || fieldType === "OBJECT") {
    return (
      <FormField
        control={control}
        name={name}
        render={({ field: formField }) => (
          <FormItem>
            <FormLabel className="text-xs">{field.display_name}</FormLabel>
            {field.description && (
              <FormDescription className="text-xs">
                {field.description}
              </FormDescription>
            )}
            <FormControl>
              <Textarea
                {...formField}
                disabled={disabled}
                placeholder='{"key": "value"}'
                className="min-h-[100px] text-xs font-mono"
                value={
                  typeof formField.value === "object" &&
                  formField.value !== null
                    ? JSON.stringify(formField.value, null, 2)
                    : (formField.value as string) || ""
                }
                onChange={(e) => {
                  try {
                    const parsed = JSON.parse(e.target.value)
                    formField.onChange(parsed)
                  } catch {
                    formField.onChange(e.target.value)
                  }
                }}
              />
            </FormControl>
            <FormMessage className="text-xs" />
          </FormItem>
        )}
      />
    )
  }

  // Default fallback for unknown field types
  return (
    <FormField
      control={control}
      name={name}
      render={({ field: formField }) => (
        <FormItem>
          <FormLabel className="text-xs">{field.display_name}</FormLabel>
          {field.description && (
            <FormDescription className="text-xs">
              {field.description}
            </FormDescription>
          )}
          <FormControl>
            <Input
              {...formField}
              value={(formField.value as string) || ""}
              disabled={disabled}
              placeholder={`Enter ${field.display_name.toLowerCase()}`}
              className="h-8 text-xs"
            />
          </FormControl>
          <FormMessage className="text-xs" />
        </FormItem>
      )}
    />
  )
}

// Helper component for array fields
function ArrayFieldInput({
  field,
  control,
  name,
  itemType,
  disabled,
}: {
  field: EntitySchemaField
  control: Control<Record<string, unknown>>
  name: string
  itemType: string
  disabled?: boolean
}) {
  const { fields, append, remove } = useFieldArray({
    control,
    name: name as never,
  })

  const getInputType = () => {
    if (itemType === "INTEGER" || itemType === "INT") return "number"
    if (itemType === "NUMBER" || itemType === "FLOAT") return "number"
    return "text"
  }

  const getPlaceholder = () => {
    if (itemType === "INTEGER" || itemType === "INT") return "0"
    if (itemType === "NUMBER" || itemType === "FLOAT") return "0.0"
    return "Enter value"
  }

  const parseValue = (value: string) => {
    if (itemType === "INTEGER" || itemType === "INT") {
      return value ? parseInt(value, 10) : null
    }
    if (itemType === "NUMBER" || itemType === "FLOAT") {
      return value ? parseFloat(value) : null
    }
    return value
  }

  return (
    <FormField
      control={control}
      name={name}
      render={() => (
        <FormItem>
          <FormLabel className="text-xs">{field.display_name}</FormLabel>
          {field.description && (
            <FormDescription className="text-xs">
              {field.description}
            </FormDescription>
          )}
          <div className="space-y-2">
            {fields.map((arrayField, index) => (
              <div key={arrayField.id} className="flex gap-2">
                <FormField
                  control={control}
                  name={`${name}.${index}`}
                  render={({ field: itemField }) => (
                    <FormItem className="flex-1">
                      <FormControl>
                        <Input
                          {...itemField}
                          value={itemField.value as string}
                          type={getInputType()}
                          step={
                            itemType === "NUMBER" || itemType === "FLOAT"
                              ? "any"
                              : undefined
                          }
                          disabled={disabled}
                          placeholder={getPlaceholder()}
                          className="h-8 text-xs"
                          onChange={(e) => {
                            itemField.onChange(parseValue(e.target.value))
                          }}
                        />
                      </FormControl>
                    </FormItem>
                  )}
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="h-8 w-8 p-0"
                  onClick={() => remove(index)}
                  disabled={disabled}
                >
                  <X className="h-3 w-3" />
                </Button>
              </div>
            ))}
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-8 text-xs"
              onClick={() =>
                append(itemType === "TEXT" || itemType === "STRING" ? "" : null)
              }
              disabled={disabled}
            >
              <Plus className="h-3 w-3 mr-1" />
              Add item
            </Button>
          </div>
          <FormMessage className="text-xs" />
        </FormItem>
      )}
    />
  )
}
