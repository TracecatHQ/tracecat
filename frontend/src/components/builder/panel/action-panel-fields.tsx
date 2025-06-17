"use client"

import React, { useCallback, useMemo, useState } from "react"
import type { ActionType, RegistryActionReadMinimal } from "@/client/types.gen"
import fuzzysort from "fuzzysort"
import {
  BracesIcon,
  ChevronDownIcon,
  CodeIcon,
  ListIcon,
  LucideIcon,
  PlusCircleIcon,
  TypeIcon,
  WorkflowIcon,
} from "lucide-react"
import {
  Controller,
  ControllerRenderProps,
  FieldValues,
  useFormContext,
} from "react-hook-form"

import { useBuilderRegistryActions } from "@/lib/hooks"
import { getType } from "@/lib/jsonschema"
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
  Command,
  CommandEmpty,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import {
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { CodeEditor } from "@/components/editor/codemirror/code-editor"
import { createTemplateRegex } from "@/components/editor/codemirror/common"
import { YamlStyledEditor } from "@/components/editor/codemirror/yaml-editor"
import { ExpressionInput } from "@/components/editor/expression-input"
import { getIcon } from "@/components/icons"
import { FieldTypeTab, PolyField } from "@/components/polymorphic-field"
import {
  CustomTagInput,
  MultiTagCommandInput,
  Suggestion,
} from "@/components/tags-input"

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
  type,
}: {
  label: string
  description?: string
  type?: string
}) {
  return (
    <FormLabel className="flex flex-col gap-1 text-xs font-medium">
      <div className="group flex items-center gap-2">
        <span className="font-semibold capitalize">{label}</span>
        {type && (
          <span className="font-mono tracking-tighter text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100">
            {type}
          </span>
        )}
      </div>
      {description && (
        <span className="text-xs text-muted-foreground">
          {formatInlineCode(description)}
        </span>
      )}
    </FormLabel>
  )
}

export function ControlledYamlField({
  label,
  fieldName,
  description,
  type,
}: {
  label: string
  fieldName: string
  description?: string
  type?: string
}) {
  const methods = useFormContext()
  const forEach = useMemo(() => methods.watch("for_each"), [methods])
  return (
    <Controller
      name={fieldName}
      control={methods.control}
      render={() => (
        <FormItem>
          <FormLabelComponent
            label={label}
            description={description}
            type={type}
          />
          <FormMessage className="whitespace-pre-line" />
          <YamlStyledEditor
            name={fieldName}
            control={methods.control}
            forEachExpressions={forEach}
          />
        </FormItem>
      )}
    />
  )
}

/**
 * Check if a value contains a template expression pattern
 *
 * Template expressions use the syntax ${{ ... }} and can contain:
 * - Action references: ${{ ACTIONS.step_name.result }}
 * - Function calls: ${{ FN.add(1, 2) }}
 * - Input references: ${{ inputs.field_name }}
 * - Secret references: ${{ SECRETS.secret_name.key }}
 * - Mixed content: "Hello ${{ inputs.name }}"
 *
 * This function is critical for field rendering logic because:
 * - Boolean fields normally render as checkboxes
 * - But if they contain expressions, they must render as text/expression inputs
 * - Same principle applies to other typed fields (numbers, selects, etc.)
 *
 * @param value - The field value to check
 * @returns true if the value contains template expression syntax
 */
function isExpression(value: unknown): boolean {
  if (typeof value !== "string") {
    return false
  }
  const regex = createTemplateRegex()
  return regex.test(value)
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
  const formattedDescription = description?.endsWith(".")
    ? description
    : `${description}.`

  // Watch the current field value to check if it's an expression
  const currentValue = methods.watch(fieldName)
  const isCurrentValueExpression = isExpression(currentValue)

  // Extract the type information
  const type = getType(fieldDefn)

  // Get all available components for this field
  const components = getTracecatComponents(fieldDefn)

  if (components.length === 0) {
    // Fallback to YAML if no components defined
    return (
      <ControlledYamlField
        label={label}
        fieldName={fieldName}
        description={formattedDescription}
        type={type}
      />
    )
  }

  // If there is only one component and it is a text or text-area, we should render an expression field
  if (components.length === 1) {
    const component = components[0]
    switch (component.component_id) {
      case "text":
        return (
          <Controller
            name={fieldName}
            control={methods.control}
            render={({ field }) => (
              <FormItem>
                <FormLabelComponent
                  label={label}
                  description={formattedDescription}
                  type={type}
                />
                <FormMessage className="whitespace-pre-line" />
                <ExpressionInput
                  value={field.value}
                  onChange={field.onChange}
                />
              </FormItem>
            )}
          />
        )
      case "text-area":
        return (
          <Controller
            name={fieldName}
            control={methods.control}
            render={({ field }) => (
              <FormItem>
                <FormLabelComponent
                  label={label}
                  description={formattedDescription}
                  type={type}
                />
                <FormMessage className="whitespace-pre-line" />
                <ExpressionInput
                  value={field.value}
                  onChange={field.onChange}
                  defaultHeight="text-area"
                />
              </FormItem>
            )}
          />
        )
      case "json":
      case "yaml":
        return (
          <ControlledYamlField
            label={label}
            fieldName={fieldName}
            description={formattedDescription}
            type={type}
          />
        )
      case "action-type":
        return (
          <Controller
            name={fieldName}
            control={methods.control}
            render={({ field }) => (
              <FormItem>
                <FormLabelComponent
                  label={label}
                  description={formattedDescription}
                  type={type}
                />
                <FormMessage className="whitespace-pre-line" />
                <ActionTypeField
                  field={field}
                  onChange={field.onChange}
                  component={component}
                />
              </FormItem>
            )}
          />
        )
    }
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
   *
   * IMPORTANT: If the current field value is an expression, force the active type to "expression"
   * regardless of the schema type. This prevents boolean fields from rendering as checkboxes
   * when they contain expressions.
   */
  let currentActiveType = activeFieldType || fieldTypes[0]?.value

  // Override the active type if the current value is an expression
  if (isCurrentValueExpression && currentActiveType !== "expression") {
    currentActiveType = "expression"
    // Update the active field type state to reflect this change
    if (activeFieldType !== "expression") {
      setActiveFieldType("expression")
    }
  }

  // Handle field type changes
  const handleFieldTypeChange = (newFieldType: string) => {
    setActiveFieldType(newFieldType)

    // If switching from expression to a native type, and the current value is an expression,
    // we should clear the field value to avoid conflicts
    if (newFieldType !== "expression" && isCurrentValueExpression) {
      // Clear the value when switching away from expression to prevent type conflicts
      methods.setValue(fieldName, "")
    }
  }

  // Find the active component by component_id
  const activeComponent: TracecatEditorComponent | undefined =
    allComponents.find(
      (component) => component.component_id === currentActiveType
    )

  // Fallback to the first component if no match is found
  const componentToRender: TracecatEditorComponent =
    activeComponent ?? allComponents[0]

  // if the component is yaml or json, we need to render the yaml editor
  if (
    componentToRender.component_id === "yaml" ||
    componentToRender.component_id === "json"
  ) {
    return (
      <ControlledYamlField
        label={label}
        fieldName={fieldName}
        description={description}
        type={type}
      />
    )
  }

  return (
    <FormField
      name={fieldName}
      control={methods.control}
      render={({ field }) => (
        <FormItem>
          <FormLabelComponent
            label={label}
            description={description}
            type={type}
          />
          <FormMessage className="whitespace-pre-line" />
          <FormControl>
            <PolyField
              fieldTypes={fieldTypes}
              activeFieldType={currentActiveType}
              onFieldTypeChange={handleFieldTypeChange}
              value={field.value}
              onChange={field.onChange}
            >
              <ComponentContent
                component={componentToRender}
                field={field}
                workspaceId={workspaceId}
                workflowId={workflowId}
              />
            </PolyField>
          </FormControl>
        </FormItem>
      )}
    />
  )
}

function ComponentContent({
  component,
  field,
  workspaceId,
  workflowId,
}: {
  component: TracecatEditorComponent
  field: ControllerRenderProps<FieldValues>
  workspaceId?: string
  workflowId?: string
}) {
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
        <CodeEditor
          value={field.value}
          onChange={field.onChange}
          language={component.lang || "python"}
          readOnly={false}
        />
      )
    case "action-type":
      return (
        <ActionTypeField
          field={field}
          onChange={field.onChange}
          component={component}
        />
      )
    case "workflow-alias":
      return <div>Workflow Alias</div>
    case "expression":
      return (
        <ExpressionInput
          value={field.value}
          onChange={field.onChange}
          placeholder="Enter an expression"
        />
      )
    default:
      return <div>Unknown component</div>
  }
}

function SingleActionTypeField({
  field,
  onChange,
  searchKeys,
}: {
  field: ControllerRenderProps<FieldValues>
  onChange: (value: string) => void
  searchKeys: (keyof RegistryActionReadMinimal)[]
}) {
  const [open, setOpen] = useState(false)
  const [searchValue, setSearchValue] = useState("")
  const { registryActions, registryActionsIsLoading } =
    useBuilderRegistryActions()

  const filterActions = useCallback(
    (actions: RegistryActionReadMinimal[], search: string) => {
      if (!search.trim()) {
        return actions.map((action) => ({ obj: action, score: 0 }))
      }

      const results = fuzzysort.go<RegistryActionReadMinimal>(search, actions, {
        all: true,
        keys: searchKeys,
      })
      return results
    },
    [searchKeys]
  )

  // Use fuzzy matching for filtering actions
  const filteredResults = useMemo(() => {
    if (!registryActions) return []
    return filterActions(registryActions, searchValue)
  }, [registryActions, searchValue])

  // Sort actions by score (fuzzy match relevance) then by namespace and name
  const sortedActions = useMemo(() => {
    return [...filteredResults].sort((a, b) => {
      // If there's a search, sort by fuzzy score first
      if (searchValue.trim()) {
        if (a.score !== b.score) {
          return b.score - a.score // Higher score first
        }
      }

      // Then sort by namespace
      const namespaceComparison = a.obj.namespace.localeCompare(b.obj.namespace)
      // If namespaces are the same, sort by name
      if (namespaceComparison === 0) {
        return a.obj.name.localeCompare(b.obj.name)
      }
      return namespaceComparison
    })
  }, [filteredResults, searchValue])

  const selectedAction = useMemo(() => {
    return registryActions?.find((action) => action.action === field.value)
  }, [registryActions, field.value])

  const handleSelect = (actionKey: string) => {
    field.onChange(actionKey)
    onChange(actionKey)
    setOpen(false)
    setSearchValue("")
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          role="combobox"
          aria-expanded={open}
          className="w-full justify-between text-left font-normal"
        >
          <div className="flex items-center gap-2 truncate">
            {selectedAction ? (
              getIcon(selectedAction.action, {
                className: "size-4 shrink-0",
              })
            ) : (
              <TypeIcon className="size-4 shrink-0" />
            )}
            <span className="truncate">
              {selectedAction
                ? selectedAction.default_title || selectedAction.action
                : "Select action type..."}
            </span>
          </div>
          <ChevronDownIcon className="ml-2 size-4 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[400px] p-0" align="start">
        <Command shouldFilter={false}>
          <CommandInput
            placeholder="Search actions..."
            value={searchValue}
            onValueChange={setSearchValue}
          />
          <ScrollArea className="h-[300px]">
            <CommandList>
              <CommandEmpty className="py-6 text-center text-xs text-muted-foreground">
                {registryActionsIsLoading
                  ? "Loading actions..."
                  : "No actions found."}
              </CommandEmpty>
              {sortedActions.map((result) => {
                const action = result.obj
                return (
                  <CommandItem
                    key={action.action}
                    value={action.action}
                    onSelect={() => handleSelect(action.action)}
                    className="cursor-pointer p-2"
                  >
                    <div className="flex w-full flex-col gap-1">
                      <div className="flex items-center gap-2">
                        {getIcon(action.action, {
                          className: "size-4 shrink-0 text-muted-foreground",
                        })}
                        <span className="font-medium">
                          {action.default_title || action.name}
                        </span>
                        {action.type === "template" && (
                          <span className="rounded bg-blue-100 px-1.5 py-0.5 text-xs text-blue-800 dark:bg-blue-900 dark:text-blue-200">
                            template
                          </span>
                        )}
                      </div>
                      <p className="line-clamp-2 text-xs text-muted-foreground">
                        {action.description}
                      </p>
                      <span className="font-mono text-xs text-muted-foreground/70">
                        {action.action}
                      </span>
                    </div>
                  </CommandItem>
                )
              })}
            </CommandList>
          </ScrollArea>
        </Command>
      </PopoverContent>
    </Popover>
  )
}

function MultipleActionTypeField({
  field,
  onChange,
  searchKeys,
}: {
  field: ControllerRenderProps<FieldValues>
  onChange: (value: string[]) => void
  searchKeys: (keyof RegistryActionReadMinimal)[]
}) {
  const { registryActions } = useBuilderRegistryActions()

  // Map actions to suggestions format for MultiTagCommandInput
  const suggestions = useMemo(() => {
    return (
      registryActions
        ?.map((action) => ({
          id: action.action,
          label: action.default_title || action.action,
          value: action.action,
          description: action.description,
          group: action.namespace,
          icon: getIcon(action.action, {
            className: "size-6 p-[3px] border-[0.5px]",
          }),
        }))
        .sort((a, b) => a.value.localeCompare(b.value)) || []
    )
  }, [registryActions])

  return (
    <MultiTagCommandInput
      value={field.value}
      onChange={onChange}
      suggestions={suggestions}
      searchKeys={searchKeys as (keyof Suggestion)[]}
    />
  )
}

export function ActionTypeField({
  field,
  onChange,
  component,
}: {
  field: ControllerRenderProps<FieldValues>
  onChange: (value: string | string[]) => void
  component?: ActionType
}) {
  const isMultiple = component?.multiple === true
  const searchKeys = [
    "action",
    "default_title",
    "description",
    "display_group",
  ] as (keyof RegistryActionReadMinimal)[]

  if (isMultiple) {
    return (
      <MultipleActionTypeField
        field={field}
        onChange={onChange as (value: string[]) => void}
        searchKeys={searchKeys}
      />
    )
  }

  return (
    <SingleActionTypeField
      field={field}
      onChange={onChange as (value: string) => void}
      searchKeys={searchKeys}
    />
  )
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
  "action-type": "Action Type",
  "workflow-alias": "Workflow Alias",
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
  "action-type": TypeIcon,
  "workflow-alias": WorkflowIcon,
}
