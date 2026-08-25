"use client"

import type {
  tracecat__registry__repositories__schemas__RegistryVersionRead,
  VersionDiff,
} from "@/client"
import { Spinner } from "@/components/loading/spinner"
import { Badge } from "@/components/ui/badge"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

/** Shorten a version string if it looks like a full commit SHA. */
export function shortVersion(version: string): string {
  return /^[0-9a-f]{40}$/i.test(version) ? version.substring(0, 12) : version
}

/** Props for {@link DiffView}. */
export interface DiffViewProps {
  diff: VersionDiff | null
  diffLoading: boolean
  versions: tracecat__registry__repositories__schemas__RegistryVersionRead[]
  compareBaseId: string | null
  compareToId: string | null
  onBaseChange: (id: string | null) => void
  onCompareChange: (id: string | null) => void
}

/** Version pickers plus the added, removed, and modified action lists of a diff. */
export function DiffView({
  diff,
  diffLoading,
  versions,
  compareBaseId,
  compareToId,
  onBaseChange,
  onCompareChange,
}: DiffViewProps) {
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-4">
        <div className="flex-1">
          <label className="mb-1 block text-sm font-medium">Base version</label>
          <Select
            value={compareBaseId ?? undefined}
            onValueChange={onBaseChange}
          >
            <SelectTrigger>
              <SelectValue placeholder="Select version" />
            </SelectTrigger>
            <SelectContent>
              {versions.map((v) => (
                <SelectItem key={v.id} value={v.id}>
                  {shortVersion(v.version)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex-1">
          <label className="mb-1 block text-sm font-medium">
            Compare to version
          </label>
          <Select
            value={compareToId ?? undefined}
            onValueChange={onCompareChange}
          >
            <SelectTrigger>
              <SelectValue placeholder="Select version" />
            </SelectTrigger>
            <SelectContent>
              {versions.map((v) => (
                <SelectItem key={v.id} value={v.id}>
                  {shortVersion(v.version)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {diffLoading && (
        <div className="flex items-center justify-center py-8">
          <Spinner className="size-6" />
        </div>
      )}

      {diff && !diffLoading && (
        <div className="space-y-4">
          <div className="flex items-center gap-4 text-sm">
            <Badge variant="secondary" className="gap-1">
              <span className="text-green-600">
                +{diff.actions_added?.length ?? 0}
              </span>
              <span>added</span>
            </Badge>
            <Badge variant="secondary" className="gap-1">
              <span className="text-red-600">
                -{diff.actions_removed?.length ?? 0}
              </span>
              <span>removed</span>
            </Badge>
            <Badge variant="secondary" className="gap-1">
              <span className="text-amber-600">
                ~{diff.actions_modified?.length ?? 0}
              </span>
              <span>modified</span>
            </Badge>
          </div>

          {diff.total_changes === 0 && (
            <div className="py-4 text-center text-muted-foreground">
              No changes between these versions
            </div>
          )}

          {(diff.actions_added?.length ?? 0) > 0 && (
            <div>
              <h4 className="mb-2 font-medium text-green-600">Added actions</h4>
              <ul className="space-y-1 font-mono text-sm">
                {diff.actions_added?.map((action) => (
                  <li key={action} className="text-muted-foreground">
                    + {action}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {(diff.actions_removed?.length ?? 0) > 0 && (
            <div>
              <h4 className="mb-2 font-medium text-red-600">Removed actions</h4>
              <ul className="space-y-1 font-mono text-sm">
                {diff.actions_removed?.map((action) => (
                  <li key={action} className="text-muted-foreground">
                    - {action}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {(diff.actions_modified?.length ?? 0) > 0 && (
            <div>
              <h4 className="mb-2 font-medium text-amber-600">
                Modified actions
              </h4>
              <ul className="space-y-2 font-mono text-sm">
                {diff.actions_modified?.map((change) => (
                  <li
                    key={change.action_name}
                    className="text-muted-foreground"
                  >
                    <div>~ {change.action_name}</div>
                    {change.description_changed && (
                      <div className="ml-4 text-xs">Description changed</div>
                    )}
                    {change.interface_changes?.map((ic, idx) => (
                      <div key={idx} className="ml-4 text-xs">
                        {ic.field} {ic.change_type}
                      </div>
                    ))}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
