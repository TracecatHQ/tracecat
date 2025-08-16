"use client"

import { format } from "date-fns"
import { Edit, MoreHorizontal, Trash2, Unlink } from "lucide-react"
import type { CaseEntityRead, CaseRecordLinkRead } from "@/client"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"

interface EntityRecordCardProps {
  recordLink: CaseRecordLinkRead
  entity?: CaseEntityRead | null
  onEdit?: () => void
  onDelete?: () => void
  onRemoveLink?: () => void
}

function renderFieldValue(value: unknown): React.ReactNode {
  if (value === null || value === undefined) {
    return <span className="text-muted-foreground">-</span>
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
  onEdit,
  onDelete,
  onRemoveLink,
}: EntityRecordCardProps) {
  const record = recordLink.record

  if (!record) {
    return null
  }

  const fieldEntries = Object.entries(record.field_data || {})
  // Show max 3 fields in collapsed view
  const displayFields = fieldEntries.slice(0, 3)
  const hasMoreFields = fieldEntries.length > 3

  return (
    <div className="group">
      <div className="rounded-lg border border-border py-3 px-4 hover:bg-accent/30 transition-colors">
        <div className="space-y-1">
          {/* Row 1: Entity name and actions */}
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-foreground">
                {entity?.display_name || entity?.name || "Entity Record"}
              </span>
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
            <div className="space-y-0.5 mt-2">
              {displayFields.map(([key, value]) => (
                <div key={key} className="flex gap-2 text-xs">
                  <span className="text-muted-foreground min-w-[80px]">
                    {key}:
                  </span>
                  {renderFieldValue(value)}
                </div>
              ))}
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
  )
}
