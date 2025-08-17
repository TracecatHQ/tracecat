"use client"

import { format } from "date-fns"
import {
  Edit,
  Link,
  MoreHorizontal,
  Network,
  Trash2,
  Unlink,
} from "lucide-react"
import type {
  CaseEntityRead,
  CaseRecordLinkRead,
  FieldMetadataRead,
} from "@/client"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useListEntityFields } from "@/lib/hooks"
import { getIconByName } from "@/lib/icon-data"
import { formatJsonWithHighlight } from "@/lib/utils"

type EntityRef = Pick<CaseEntityRead, "id" | "name">

interface EntityRecordCardProps {
  recordLink: CaseRecordLinkRead
  entity?: CaseEntityRead | null
  entities?: EntityRef[]
  workspaceId?: string
  onEdit?: () => void
  onDelete?: () => void
  onRemoveLink?: () => void
}

function renderFieldValue(
  value: unknown,
  isRelation: boolean = false
): React.ReactNode {
  if (value === null || value === undefined) {
    return <span className="text-muted-foreground">-</span>
  }

  // Handle relation fields (they contain resolved field_data)
  if (isRelation) {
    if (Array.isArray(value)) {
      // RELATION_HAS_MANY
      if (value.length === 0) {
        return (
          <span className="text-xs text-muted-foreground">
            No related records
          </span>
        )
      }
      // Format with highlighted keys
      return <span className="text-xs">{formatJsonWithHighlight(value)}</span>
    } else if (typeof value === "object" && value !== null) {
      // RELATION_BELONGS_TO
      // Format with highlighted keys
      return <span className="text-xs">{formatJsonWithHighlight(value)}</span>
    }
  }

  // Handle different field types based on value
  if (typeof value === "boolean") {
    return <span className="text-xs">{value ? "Yes" : "No"}</span>
  }

  if (typeof value === "number") {
    return <span className="text-xs">{value.toLocaleString()}</span>
  }

  if (typeof value === "string") {
    // Check if it's a date string (ISO format)
    if (/^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2})?/.test(value)) {
      try {
        const date = new Date(value)
        if (!isNaN(date.getTime())) {
          // Check if it includes time
          if (value.includes("T")) {
            return (
              <span className="text-xs">
                {format(date, "MMM dd, yyyy HH:mm")}
              </span>
            )
          }
          return <span className="text-xs">{format(date, "MMM dd, yyyy")}</span>
        }
      } catch {
        // Not a valid date, treat as string
      }
    }

    // Truncate long strings
    if (value.length > 60) {
      return (
        <span className="text-xs truncate block" title={value}>
          {value.substring(0, 60)}...
        </span>
      )
    }
    return <span className="text-xs">{value}</span>
  }

  if (Array.isArray(value)) {
    if (value.length === 0) {
      return <span className="text-xs text-muted-foreground">Empty list</span>
    }
    // Simple comma-separated list
    return (
      <span className="text-xs">
        {value.map((item) => String(item)).join(", ")}
      </span>
    )
  }

  if (typeof value === "object" && value !== null) {
    // Handle relation objects
    const obj = value as Record<string, unknown>
    if (obj.id || obj.name) {
      return (
        <span className="text-xs">
          {(obj.name as string) || (obj.id as string)}
        </span>
      )
    }
    // For complex objects, just show key count
    return (
      <span className="text-xs text-muted-foreground">
        Object ({Object.keys(obj).length} fields)
      </span>
    )
  }

  // Fallback for any other type
  return <span className="text-xs">{String(value)}</span>
}

export function EntityRecordCard({
  recordLink,
  entity,
  entities,
  workspaceId,
  onEdit,
  onDelete,
  onRemoveLink,
}: EntityRecordCardProps) {
  const record = recordLink.record

  // Fetch field metadata if we have entity_id and workspaceId
  const entityId = record?.entity_id || recordLink.entity_id
  const { fields: fieldMetadata } = useListEntityFields({
    entityId,
    workspaceId: workspaceId || "",
    includeInactive: false,
  })

  if (!record) {
    return null
  }

  const fieldEntries = Object.entries(record.field_data || {})
  const relationFields = record.relation_fields || []
  // Show max 3 fields in collapsed view
  const displayFields = fieldEntries.slice(0, 3)
  const hasMoreFields = fieldEntries.length > 3

  // Helper function to get field metadata by key
  const getFieldMetadata = (
    fieldKey: string
  ): FieldMetadataRead | undefined => {
    return fieldMetadata?.find((f) => f.field_key === fieldKey)
  }

  return (
    <TooltipProvider>
      <div className="group">
        <div className="rounded-lg border border-border py-3 px-4 hover:bg-accent/30 transition-colors">
          <div className="space-y-2">
            {/* Row 1: Entity name and actions */}
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <Avatar className="size-5">
                  <AvatarFallback className="text-xs">
                    {entity?.icon
                      ? (() => {
                          const Icon = getIconByName(entity.icon)
                          return Icon ? (
                            <Icon className="size-3" />
                          ) : (
                            (
                              entity?.display_name?.[0] ||
                              entity?.name?.[0] ||
                              "?"
                            ).toUpperCase()
                          )
                        })()
                      : (
                          entity?.display_name?.[0] ||
                          entity?.name?.[0] ||
                          "?"
                        ).toUpperCase()}
                  </AvatarFallback>
                </Avatar>
                {entity?.name ? (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span className="text-sm font-medium text-foreground cursor-default">
                        {entity.display_name || entity.name}
                      </span>
                    </TooltipTrigger>
                    <TooltipContent>
                      <span className="text-xs">{entity.name}</span>
                    </TooltipContent>
                  </Tooltip>
                ) : (
                  <span className="text-sm font-medium text-foreground">
                    {entity?.display_name || "Entity Record"}
                  </span>
                )}
                {fieldEntries.length > 0 && (
                  <span className="text-xs text-muted-foreground">
                    {fieldEntries.length} field
                    {fieldEntries.length !== 1 ? "s" : ""}
                  </span>
                )}
              </div>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-6 rounded-md opacity-0 group-hover:opacity-100 data-[state=open]:bg-accent data-[state=open]:text-accent-foreground data-[state=open]:opacity-100"
                  >
                    <MoreHorizontal className="size-4" />
                    <span className="sr-only">More options</span>
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  {onEdit && (
                    <DropdownMenuItem
                      className="flex cursor-pointer items-center gap-2 text-xs"
                      onClick={onEdit}
                    >
                      <Edit className="size-3" />
                      Edit
                    </DropdownMenuItem>
                  )}
                  {onRemoveLink && (
                    <DropdownMenuItem
                      className="flex cursor-pointer items-center gap-2 text-xs"
                      onClick={onRemoveLink}
                    >
                      <Unlink className="size-3" />
                      Unlink from case
                    </DropdownMenuItem>
                  )}
                  {onDelete && (
                    <DropdownMenuItem
                      className="flex cursor-pointer items-center gap-2 text-xs text-destructive focus:text-destructive"
                      onClick={onDelete}
                    >
                      <Trash2 className="size-3" />
                      Delete
                    </DropdownMenuItem>
                  )}
                </DropdownMenuContent>
              </DropdownMenu>
            </div>

            {/* Row 2+: Field data */}
            {fieldEntries.length === 0 ? (
              <p className="text-xs text-muted-foreground">No field data</p>
            ) : (
              <div className="space-y-1">
                {displayFields.map(([key, value]) => {
                  const isRelation = relationFields.includes(key)
                  const fieldMeta = getFieldMetadata(key)
                  const fieldType = fieldMeta?.field_type
                  const isBelongsTo = fieldType === "RELATION_BELONGS_TO"
                  const isHasMany = fieldType === "RELATION_HAS_MANY"
                  const isRelationField = isBelongsTo || isHasMany

                  // Build tooltip content for relation fields
                  let tooltipContent = ""
                  if (isRelationField) {
                    const currentEntityName = entity?.name || "record"
                    let targetEntityName = "related"

                    if (fieldMeta?.target_entity_id && entities) {
                      const targetEntity = entities.find(
                        (e) => e.id === fieldMeta.target_entity_id
                      )
                      if (targetEntity) {
                        targetEntityName = targetEntity.name
                      }
                    }

                    tooltipContent = isBelongsTo
                      ? `One ${currentEntityName} to one ${targetEntityName}`
                      : `One ${currentEntityName} to many ${targetEntityName}`
                  }

                  const Icon = isBelongsTo ? Link : isHasMany ? Network : null

                  return (
                    <div key={key} className="flex gap-2 text-xs">
                      <span className="text-[hsl(var(--field-key))] w-32 flex items-center">
                        {Icon && (
                          <Icon className="mr-1 h-3 w-3 flex-shrink-0" />
                        )}
                        {isRelationField ? (
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <span className="cursor-default truncate">
                                {key.length > 24
                                  ? `${key.substring(0, 24)}...`
                                  : key}
                              </span>
                            </TooltipTrigger>
                            <TooltipContent>
                              <div className="space-y-1">
                                {key.length > 24 && (
                                  <div className="font-medium">{key}</div>
                                )}
                                <div>{tooltipContent}</div>
                              </div>
                            </TooltipContent>
                          </Tooltip>
                        ) : key.length > 24 ? (
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <span className="cursor-default truncate">
                                {key.substring(0, 24)}...
                              </span>
                            </TooltipTrigger>
                            <TooltipContent>{key}</TooltipContent>
                          </Tooltip>
                        ) : (
                          <span className="truncate">{key}</span>
                        )}
                        <span className="ml-auto">:</span>
                      </span>
                      {renderFieldValue(value, isRelation)}
                    </div>
                  )
                })}
                {hasMoreFields && (
                  <span className="text-xs text-muted-foreground italic">
                    +{fieldEntries.length - 3} more field
                    {fieldEntries.length - 3 !== 1 ? "s" : ""}
                  </span>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </TooltipProvider>
  )
}
