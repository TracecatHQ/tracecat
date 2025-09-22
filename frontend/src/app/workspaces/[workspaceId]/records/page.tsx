"use client"

import { useState } from "react"
import { RecordsTable } from "@/components/records/records-table"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useEntities } from "@/hooks/use-entities"
import { getIconByName } from "@/lib/icons"
import { useWorkspaceId } from "@/providers/workspace-id"

export default function RecordsPage() {
  const workspaceId = useWorkspaceId()
  const { entities } = useEntities(workspaceId)
  const [entityFilter, setEntityFilter] = useState<string | null>(null)

  return (
    <div className="size-full overflow-auto p-6 space-y-6">
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2">
          <Label
            htmlFor="entity-filter"
            className="text-xs text-muted-foreground"
          >
            Entity
          </Label>
          <Select
            value={entityFilter || "all"}
            onValueChange={(value) =>
              setEntityFilter(value === "all" ? null : value)
            }
          >
            <SelectTrigger id="entity-filter" className="h-7 w-[200px]">
              <SelectValue placeholder="All entities">
                {entityFilter && entities
                  ? (() => {
                      const selectedEntity = entities.find(
                        (e) => e.id === entityFilter
                      )
                      const IconComponent = selectedEntity?.icon
                        ? getIconByName(selectedEntity.icon)
                        : null
                      return (
                        <div className="flex items-center gap-2">
                          {IconComponent && (
                            <IconComponent className="h-3.5 w-3.5 text-muted-foreground" />
                          )}
                          <span>{selectedEntity?.display_name}</span>
                        </div>
                      )
                    })()
                  : "All entities"}
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All entities</SelectItem>
              {entities?.map((entity) => {
                const IconComponent = entity.icon
                  ? getIconByName(entity.icon)
                  : null
                return (
                  <SelectItem key={entity.id} value={entity.id}>
                    <div className="flex items-center gap-2">
                      {IconComponent && (
                        <IconComponent className="h-3.5 w-3.5 text-muted-foreground" />
                      )}
                      <span>{entity.display_name}</span>
                    </div>
                  </SelectItem>
                )
              })}
            </SelectContent>
          </Select>
        </div>
      </div>

      <RecordsTable entityFilter={entityFilter} />
    </div>
  )
}
