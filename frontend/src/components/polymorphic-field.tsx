"use client"

import { cva } from "class-variance-authority"
import * as React from "react"
import { useState } from "react"
import { Input, type InputProps, inputVariants } from "@/components/ui/input"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import type { TracecatComponentId } from "@/lib/schema"
import { cn } from "@/lib/utils"

/**
 * Configuration for a field type tab
 */
export interface FieldTypeTab {
  /** Unique identifier for the tab */
  value: TracecatComponentId
  /** Display label for the tab */
  label: string
  /** Icon component to display in the tab (optional) */
  icon?: React.ComponentType<{ className?: string }>
  /** Tooltip text for the tab (optional) */
  tooltip?: string
}

/**
 * Props for the PolyField component
 */
export interface PolymorphicFieldProps extends Omit<InputProps, "children"> {
  /** Array of field type tabs to display */
  fieldTypes: FieldTypeTab[]
  /** Currently active field type */
  activeFieldType?: TracecatComponentId
  /** Default field type (used if activeFieldType is not provided) */
  defaultFieldType?: TracecatComponentId
  /** Callback fired when field type changes */
  onFieldTypeChange?: (fieldType: TracecatComponentId) => void
  /** Child components representing different field types */
  children: React.ReactNode
  /** Additional className for the wrapper */
  wrapperClassName?: string
  /** Whether to show the tabs */
  showTabs?: boolean
}

const typedInputVariants = cva("relative w-full", {
  variants: {
    tabPosition: {
      "top-right": "flex flex-col",
      "top-left": "flex flex-col",
    },
  },
  defaultVariants: {
    tabPosition: "top-right",
  },
})

/**
 * PolyField - A wrapper component that extends Input with configurable tabs
 * and field types. Each tab corresponds to a different field type that can be
 * rendered as children.
 */
export const PolyField = React.forwardRef<
  HTMLDivElement,
  PolymorphicFieldProps
>(
  (
    {
      fieldTypes,
      activeFieldType,
      defaultFieldType,
      onFieldTypeChange,
      children,
      className,
      wrapperClassName,
      showTabs = true,
      variant,
      ...inputProps
    },
    ref
  ) => {
    const [internalActiveType, setInternalActiveType] = useState<string>(
      activeFieldType ?? defaultFieldType ?? fieldTypes[0]?.value ?? "default"
    )

    const currentActiveType = activeFieldType ?? internalActiveType

    const handleFieldTypeChange = (fieldType: string) => {
      if (activeFieldType === undefined) {
        setInternalActiveType(fieldType)
      }
      onFieldTypeChange?.(fieldType as TracecatComponentId)
    }

    if (fieldTypes.length === 0) {
      return (
        <div ref={ref} className={cn("relative w-full", wrapperClassName)}>
          <Input
            className={cn(inputVariants({ variant }), className)}
            {...inputProps}
          />
        </div>
      )
    }

    return (
      <div
        ref={ref}
        className={cn(
          typedInputVariants({ tabPosition: "top-right" }),
          wrapperClassName
        )}
      >
        <Tabs
          value={currentActiveType}
          onValueChange={handleFieldTypeChange}
          className="w-full"
        >
          {/* Tab content for different field types with tabs positioned on top-right */}
          {fieldTypes.map((fieldType) => (
            <TabsContent
              key={fieldType.value}
              value={fieldType.value}
              className="group relative mt-0"
            >
              {children}

              {/* Tabs positioned on top-right exterior of content */}
              {showTabs && (
                <div className="absolute -top-7 right-0 opacity-0 transition-opacity duration-100 group-focus-within:opacity-100 group-hover:opacity-100">
                  <TabsList className="h-6 rounded-sm bg-muted p-0.5">
                    {fieldTypes.map((fieldType) => {
                      return (
                        <TabsTrigger
                          key={fieldType.value}
                          value={fieldType.value}
                          className="h-5 rounded-sm px-1.5 text-xs data-[state=active]:bg-primary data-[state=active]:text-primary-foreground data-[state=active]:shadow-none"
                          title={fieldType.tooltip}
                        >
                          <span className="text-xs">{fieldType.label}</span>
                        </TabsTrigger>
                      )
                    })}
                  </TabsList>
                </div>
              )}
            </TabsContent>
          ))}
        </Tabs>
      </div>
    )
  }
)

PolyField.displayName = "PolyField"

/**
 * Hook to manage field type state
 */
export function useFieldType(
  fieldTypes: FieldTypeTab[],
  defaultFieldType?: string
) {
  const [activeFieldType, setActiveFieldType] = useState<string>(
    defaultFieldType ?? fieldTypes[0]?.value ?? "default"
  )

  const currentFieldType = fieldTypes.find((ft) => ft.value === activeFieldType)

  return {
    activeFieldType,
    setActiveFieldType,
    currentFieldType,
    fieldTypes,
  }
}
