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
  TypeIcon,
  WorkflowIcon,
} from "lucide-react"
import {
  Controller,
  ControllerRenderProps,
  FieldValues,
  useFormContext,
} from "react-hook-form"

import { isExpression } from "@/lib/expressions"
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
  type = "any",
}: {
  label?: string
  description?: string
  type?: string
}) {
  return (
    (label || type || description) && (
      <FormLabel className="flex flex-col gap-1 text-xs font-medium">
        <div className="group flex items-center gap-2">
          {label && <span className="font-semibold capitalize">{label}</span>}
          {type && (
            <span className="font-mono tracking-tighter text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100">
              {type}
            </span>
          )}
        </div>
        {description && (
          <span className="text-xs text-muted-foreground">
            {formatInlineCode(
              !description.trim().endsWith(".")
                ? description + "."
                : description
            )}
          </span>
        )}
      </FormLabel>
    )
  )
}

export function ControlledYamlField({
  fieldName,
  label,
  description,
  type,
}: {
  fieldName: string
  label?: string
  description?: string
  type?: string
}) {
  const methods = useFormContext()
  const forEach = methods.watch("for_each")
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
}: FormComponentProps) {
  const methods = useFormContext()
  const { description } = fieldDefn
  const formattedDescription = description?.endsWith(".")
    ? description
    : `${description}.`

  // Watch the current field value to check if it's an expression
  const currentValue = methods.watch(fieldName)
  const isCurrentValueExpression = isExpression(currentValue)
  const [activeFieldType, setActiveFieldType] = useState<
    TracecatComponentId | undefined
  >(() => {
    if (isCurrentValueExpression) {
      return "expression"
    }
    return undefined
  })

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
                  placeholder="Enter text or @ to begin an expression..."
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
                  placeholder="Enter text or @ to begin an expression..."
                />
              </FormItem>
            )}
          />
        )
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

  // Handle field type changes
  const handleFieldTypeChange = (newFieldType: TracecatComponentId) => {
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
      (component) => component.component_id === activeFieldType
    )

  // Fallback to the first component if no match is found
  const componentToRender: TracecatEditorComponent =
    activeComponent ?? allComponents[0]

  // if the component is yaml, we need to render the yaml editor
  if (componentToRender.component_id === "yaml") {
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
              activeFieldType={activeFieldType}
              onFieldTypeChange={handleFieldTypeChange}
              value={field.value}
              onChange={field.onChange}
            >
              <ComponentContent component={componentToRender} field={field} />
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
}: {
  component: TracecatEditorComponent
  field: ControllerRenderProps<FieldValues>
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
    case "integer":
      return (
        <Input
          type="number"
          value={field.value || ""}
          min={component.min_val ?? undefined}
          max={component.max_val ?? undefined}
          step={component.step || 1}
          onChange={(e) =>
            field.onChange(
              e.target.value ? parseInt(e.target.value) : undefined
            )
          }
        />
      )
    case "float":
      return (
        <Input
          type="number"
          value={field.value || ""}
          min={component.min_val ?? undefined}
          max={component.max_val ?? undefined}
          step={component.step || 0.1}
          onChange={(e) =>
            field.onChange(
              e.target.value ? parseFloat(e.target.value) : undefined
            )
          }
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
    // Expression, workflow alias, and other fields fallback to expression
    case "workflow-alias":
    case "expression":
    default:
      return <ExpressionInput value={field.value} onChange={field.onChange} />
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
  integer: "Number",
  float: "Decimal",
  toggle: "Toggle",
  code: "Code",
  yaml: "YAML",
  expression: "Expression",
  "action-type": "Action Type",
  "workflow-alias": "Workflow Alias",
}

const COMPONENT_ICONS: Record<TracecatComponentId, LucideIcon> = {
  text: TypeIcon,
  "text-area": TypeIcon,
  select: ListIcon,
  "tag-input": ListIcon,
  integer: TypeIcon,
  float: TypeIcon,
  toggle: TypeIcon,
  code: CodeIcon,
  yaml: CodeIcon,
  expression: BracesIcon,
  "action-type": TypeIcon,
  "workflow-alias": WorkflowIcon,
}
