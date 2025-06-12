"use client"

import React, { useEffect, useState } from "react"
import { Code } from "@/client"
import { useWorkflow } from "@/providers/workflow"
import { useWorkspace } from "@/providers/workspace"
import { Tag } from "emblor"
import {
  BracesIcon,
  CodeIcon,
  ListIcon,
  LucideIcon,
  PlusCircleIcon,
  TypeIcon,
} from "lucide-react"
import {
  ControllerRenderProps,
  FieldValues,
  useFormContext,
} from "react-hook-form"

import {
  ExpressionComponent,
  getTracecatComponents,
  TracecatComponentId,
  TracecatEditorComponent,
  TracecatJsonSchema,
} from "@/lib/schema"
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
import { JsonStyledEditor } from "@/components/editor/json-editor"
import { FieldTypeTab, PolyField } from "@/components/polymorphic-field"
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
              <SelectTrigger>
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

/**
 * TagInputField renders a tag input field for form usage.
 * It synchronizes the tags state with the form field value using useEffect,
 * and ensures only the tag text values are submitted.
 *
 * @param label - The label for the field
 * @param fieldName - The name of the field
 * @param fieldDefn - The field definition (for description)
 */
export function TagInputField({
  label,
  fieldName,
  fieldDefn,
}: FormComponentProps) {
  const methods = useFormContext()
  const { description } = fieldDefn
  const [tags, setTags] = useState<Tag[]>([])

  // Synchronize tags state with the form field value
  // This effect runs when the field value changes
  useEffect(() => {
    // Get the current value from the form
    const fieldValue = methods.getValues(`inputs.${fieldName}`)
    if (fieldValue && Array.isArray(fieldValue)) {
      const tagObjects = fieldValue.map((value: string, index: number) => ({
        id: `${index}`,
        text: value,
      }))
      setTags(tagObjects)
    } else {
      setTags([])
    }
    // We intentionally depend on the form value for this field
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [methods.watch(`inputs.${fieldName}`), fieldName, methods])

  /**
   * Handles setting tags and updates the form field value.
   * @param newTags - The new tags to set
   */
  const handleSetTags = (newTags: React.SetStateAction<Tag[]>) => {
    setTags(newTags)
    // Extract only the text values for the form field
    const resolvedTags = typeof newTags === "function" ? newTags(tags) : newTags
    methods.setValue(
      `inputs.${fieldName}`,
      resolvedTags.map((tag) => tag.text)
    )
  }

  return (
    <FormField
      name={`inputs.${fieldName}`}
      control={methods.control}
      render={() => (
        <FormItem>
          <FormLabelComponent label={label} description={description} />
          <FormMessage className="whitespace-pre-line" />
          <FormControl>
            <CustomTagInput
              tags={tags}
              setTags={handleSetTags}
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
                      className="flex-1 text-xs"
                    />
                    <Input
                      type="text"
                      placeholder="Value"
                      value={value as string}
                      onChange={(e) => updateValue(key, e.target.value)}
                      className="flex-1 text-xs"
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

export function ExpressionField({
  label,
  fieldName,
  fieldDefn,
  workspaceId,
  workflowId,
}: FormComponentProps & {
  workspaceId?: string
  workflowId?: string
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
            <DynamicCustomEditor
              className="h-64 w-full"
              value={field.value}
              onChange={field.onChange}
              defaultLanguage="yaml-extended"
              workspaceId={workspaceId}
              workflowId={workflowId}
            />
          </FormControl>
        </FormItem>
      )}
    />
  )
}

/**
 * PolymorphicField renders a field with multiple possible input types (components),
 * always including an "expression" component as the last option.
 * This ensures users can always select an expression input, regardless of the schema.
 *
 * @param label - The field label
 * @param fieldName - The field name in the form
 * @param fieldDefn - The JSON schema definition for the field
 * @param workspaceId - Optional workspace ID for context
 * @param workflowId - Optional workflow ID for context
 */
export function PolymorphicField({
  label,
  fieldName,
  fieldDefn,
  workspaceId,
  workflowId,
}: FormComponentProps & {
  workspaceId?: string
  workflowId?: string
}) {
  const methods = useFormContext()
  const { description } = fieldDefn
  const [activeFieldType, setActiveFieldType] = useState<string>()

  // Get all available components for this field
  const components = getTracecatComponents(fieldDefn)

  if (components.length === 0) {
    // Fallback to YAML if no components defined
    return (
      <YamlField
        label={label}
        fieldName={fieldName}
        description={description}
      />
    )
  }

  /**
   * Build the list of field types for the polymorphic field selector,
   * falling back to YAML if no components are defined or if components is not an array.
   * Always includes the "expression" component as the last option if components exist.
   */
  // Compute the list of field types for the polymorphic field selector without using an IIFE.
  let fieldTypes: FieldTypeTab[] = []

  // If components is not an array or is empty, fallback to YAML only
  if (!Array.isArray(components) || components.length === 0) {
    fieldTypes = [
      {
        value: "yaml",
        label: COMPONENT_LABELS["yaml"],
        icon: COMPONENT_ICONS["yaml"],
        tooltip: "Use YAML input",
      },
    ]
  } else {
    // Otherwise, build the list of field types from components, always append "expression"
    fieldTypes = [
      ...components
        .filter(
          (
            component
          ): component is TracecatEditorComponent & {
            component_id: TracecatComponentId
          } => component.component_id !== undefined
        )
        .map((component) => {
          const componentId = component.component_id
          return {
            value: componentId,
            label: COMPONENT_LABELS[componentId],
            icon: COMPONENT_ICONS[componentId],
            tooltip: `Use ${COMPONENT_LABELS[componentId]} input`,
          }
        }),
      {
        value: "expression",
        label: COMPONENT_LABELS["expression"],
        icon: COMPONENT_ICONS["expression"],
        tooltip: "Use Expression input",
      },
    ]
  }

  // Compose the list of components to render, always including the expression component last.
  const allComponents: TracecatEditorComponent[] = [
    ...components,
    { component_id: "expression" } as ExpressionComponent,
  ]

  /**
   * Determine the active component to render based on the current activeFieldType.
   * If no matching component is found, fallback to the first component in allComponents.
   */
  const currentActiveType = activeFieldType || fieldTypes[0]?.value

  // Find the active component by component_id
  const activeComponent: TracecatEditorComponent | undefined =
    allComponents.find(
      (component) => component.component_id === currentActiveType
    )

  // Fallback to the first component if no match is found
  const componentToRender: TracecatEditorComponent =
    activeComponent ?? allComponents[0]

  return (
    <FormField
      name={`inputs.${fieldName}`}
      control={methods.control}
      render={({ field }) => (
        <FormItem>
          <FormLabelComponent label={label} description={description} />
          <FormMessage className="whitespace-pre-line" />
          <FormControl>
            <PolyField
              fieldTypes={fieldTypes}
              activeFieldType={activeFieldType}
              onFieldTypeChange={setActiveFieldType}
              value={field.value}
              onChange={field.onChange}
            >
              {renderComponentContent(
                componentToRender,
                field,
                workspaceId,
                workflowId
              )}
            </PolyField>
          </FormControl>
        </FormItem>
      )}
    />
  )
}

function renderSingleComponent(
  component: TracecatEditorComponent,
  label: string,
  fieldName: string,
  fieldDefn: TracecatJsonSchema
) {
  // Render the appropriate single component based on component_id
  switch (component.component_id) {
    case "text":
      return (
        <TextField label={label} fieldName={fieldName} fieldDefn={fieldDefn} />
      )
    case "text-area":
      return (
        <TextAreaField
          label={label}
          fieldName={fieldName}
          fieldDefn={fieldDefn}
          rows={component.rows}
          placeholder={component.placeholder}
        />
      )
    case "select":
      return (
        <SelectField
          label={label}
          fieldName={fieldName}
          fieldDefn={fieldDefn}
          options={component.options ?? []}
          multiple={component.multiple}
        />
      )
    case "tag-input":
      return (
        <TagInputField
          label={label}
          fieldName={fieldName}
          fieldDefn={fieldDefn}
        />
      )
    case "key-value":
      return (
        <KeyValueField
          label={label}
          fieldName={fieldName}
          fieldDefn={fieldDefn}
        />
      )
    case "integer":
      return (
        <IntegerField
          label={label}
          fieldName={fieldName}
          fieldDefn={fieldDefn}
          minVal={component.min_val ?? undefined}
          maxVal={component.max_val ?? undefined}
          step={component.step}
        />
      )
    case "float":
      return (
        <FloatField
          label={label}
          fieldName={fieldName}
          fieldDefn={fieldDefn}
          minVal={component.min_val}
          maxVal={component.max_val}
          step={component.step}
        />
      )
    case "toggle":
      return (
        <ToggleField
          label={label}
          fieldName={fieldName}
          fieldDefn={fieldDefn}
          labelOn={component.label_on}
          labelOff={component.label_off}
        />
      )
    case "code":
      return (
        <CodeMirrorCodeField
          label={label}
          fieldName={fieldName}
          fieldDefn={fieldDefn}
          code={component}
        />
      )
    case "yaml":
      return (
        <YamlField
          label={label}
          fieldName={fieldName}
          description={fieldDefn.description}
        />
      )
    default:
      return (
        <YamlField
          label={label}
          fieldName={fieldName}
          description={fieldDefn.description}
        />
      )
  }
}

function renderComponentContent(
  component: TracecatEditorComponent,
  field: ControllerRenderProps<FieldValues>,
  workspaceId?: string,
  workflowId?: string
) {
  switch (component.component_id) {
    case "text":
      return <Input type="text" value={field.value} onChange={field.onChange} />
    case "text-area":
      return (
        <Textarea
          rows={component.rows || 4}
          placeholder={component.placeholder || ""}
          value={field.value}
          onChange={field.onChange}
        />
      )
    case "select":
      return (
        <Select value={field.value} onValueChange={field.onChange}>
          <SelectTrigger>
            <SelectValue placeholder="Select an option" />
          </SelectTrigger>
          <SelectContent>
            {component.options?.map((option: string) => (
              <SelectItem key={option} value={option}>
                {option}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )
    case "tag-input":
      return (
        <CustomTagInput
          tags={
            Array.isArray(field.value)
              ? field.value.map((v: string, i: number) => ({
                  id: `${i}`,
                  text: v,
                }))
              : []
          }
          setTags={(newTags) => {
            const resolvedTags =
              typeof newTags === "function" ? newTags([]) : newTags
            field.onChange(resolvedTags.map((tag) => tag.text))
          }}
          placeholder="Add values..."
        />
      )
    case "key-value":
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
        <div className="space-y-2">
          {pairs.map(([key, value], index) => (
            <div key={index} className="flex items-center gap-2">
              <Input
                type="text"
                placeholder="Key"
                value={key}
                onChange={(e) => updateKey(key, e.target.value)}
                className="flex-1 text-xs"
              />
              <Input
                type="text"
                placeholder="Value"
                value={value as string}
                onChange={(e) => updateValue(key, e.target.value)}
                className="flex-1 text-xs"
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
      )
    case "integer":
      return (
        <Input
          type="number"
          value={field.value}
          min={component.min_val ?? undefined}
          max={component.max_val ?? undefined}
          step={component.step || 1}
          onChange={field.onChange}
        />
      )
    case "float":
      return (
        <Input
          type="number"
          value={field.value}
          min={component.min_val}
          max={component.max_val}
          step={component.step || 0.1}
          onChange={field.onChange}
        />
      )
    case "toggle":
      return (
        <div className="flex items-center space-x-2">
          <Checkbox checked={field.value} onCheckedChange={field.onChange} />
          <span className="text-sm text-muted-foreground">
            {field.value
              ? component.label_on || "On"
              : component.label_off || "Off"}
          </span>
        </div>
      )
    case "code":
      return (
        <CodeMirrorEditor
          value={field.value}
          onChange={field.onChange}
          language={component.lang || "python"}
          readOnly={false}
        />
      )
    case "yaml":
      return (
        <DynamicCustomEditor
          className="h-64 w-full"
          value={field.value}
          onChange={field.onChange}
          defaultLanguage="yaml-extended"
          workspaceId={workspaceId}
          workflowId={workflowId}
        />
      )
    case "json":
      return (
        <JsonStyledEditor value={field.value || ""} setValue={field.onChange} />
      )
    case "expression":
      return (
        <Input
          value={field.value}
          onChange={field.onChange}
          placeholder="Enter an expression"
        />
      )
    default:
      return (
        <DynamicCustomEditor
          className="h-64 w-full"
          value={field.value}
          onChange={field.onChange}
          defaultLanguage="yaml-extended"
          workspaceId={workspaceId}
          workflowId={workflowId}
        />
      )
  }
}

const COMPONENT_LABELS: Record<TracecatComponentId, string> = {
  text: "Text",
  "text-area": "Text Area",
  select: "Select",
  "tag-input": "Tags",
  "key-value": "Key-Value",
  integer: "Number",
  float: "Decimal",
  toggle: "Toggle",
  code: "Code",
  yaml: "YAML",
  json: "JSON",
  expression: "Expression",
}

const COMPONENT_ICONS: Record<TracecatComponentId, LucideIcon> = {
  text: TypeIcon,
  "text-area": TypeIcon,
  select: ListIcon,
  "tag-input": ListIcon,
  "key-value": ListIcon,
  integer: TypeIcon,
  float: TypeIcon,
  toggle: TypeIcon,
  code: CodeIcon,
  yaml: CodeIcon,
  json: CodeIcon,
  expression: BracesIcon,
}
