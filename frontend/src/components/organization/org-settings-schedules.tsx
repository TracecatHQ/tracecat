"use client"

import { RefreshCwIcon } from "lucide-react"
import { useMemo, useState } from "react"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { useOrgScheduleSync } from "@/hooks/use-org-schedule-sync"

function formatTimestamp(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return "Unknown"
  }
  return date.toLocaleString()
}

function formatShortId(value: string): string {
  if (value.length <= 12) {
    return value
  }
  return `${value.slice(0, 8)}...`
}

export function OrgSettingsSchedules() {
  const [dialogOpen, setDialogOpen] = useState(false)
  const {
    scheduleSync,
    scheduleSyncIsLoading,
    scheduleSyncIsFetching,
    scheduleSyncError,
    refreshScheduleSync,
    recreateMissingSchedules,
    recreateMissingSchedulesIsPending,
  } = useOrgScheduleSync()

  const summary = useMemo(
    () =>
      scheduleSync?.summary ?? {
        total_schedules: 0,
        present_count: 0,
        missing_count: 0,
      },
    [scheduleSync?.summary]
  )
  const items = scheduleSync?.items ?? []

  if (scheduleSyncIsLoading) {
    return <CenteredSpinner />
  }
  if (scheduleSyncError) {
    return (
      <AlertNotification
        level="error"
        message={`Error loading organization schedule sync status: ${scheduleSyncError.message}`}
      />
    )
  }

  return (
    <div className="space-y-6">
      <div className="grid gap-3 md:grid-cols-3">
        <div className="rounded-lg border p-4">
          <p className="text-xs uppercase tracking-wide text-muted-foreground">
            Total schedules
          </p>
          <p className="mt-2 text-2xl font-semibold">
            {summary.total_schedules}
          </p>
        </div>
        <div className="rounded-lg border p-4">
          <p className="text-xs uppercase tracking-wide text-muted-foreground">
            Present in Temporal
          </p>
          <p className="mt-2 text-2xl font-semibold">{summary.present_count}</p>
        </div>
        <div className="rounded-lg border p-4">
          <p className="text-xs uppercase tracking-wide text-muted-foreground">
            Missing in Temporal
          </p>
          <p className="mt-2 text-2xl font-semibold">{summary.missing_count}</p>
        </div>
      </div>

      <div className="flex items-center gap-2">
        <Button
          type="button"
          variant="outline"
          onClick={() => refreshScheduleSync()}
          disabled={scheduleSyncIsFetching || recreateMissingSchedulesIsPending}
        >
          <RefreshCwIcon className="mr-2 size-4" />
          Refresh status
        </Button>

        <AlertDialog open={dialogOpen} onOpenChange={setDialogOpen}>
          <AlertDialogTrigger asChild>
            <Button
              type="button"
              disabled={
                summary.missing_count === 0 || recreateMissingSchedulesIsPending
              }
            >
              Create missing Temporal schedules
            </Button>
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>
                Recreate missing Temporal schedules
              </AlertDialogTitle>
              <AlertDialogDescription>
                This will attempt to create {summary.missing_count} missing
                schedule{summary.missing_count === 1 ? "" : "s"} in Temporal.
                Existing schedules will be skipped.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel disabled={recreateMissingSchedulesIsPending}>
                Cancel
              </AlertDialogCancel>
              <AlertDialogAction
                disabled={recreateMissingSchedulesIsPending}
                onClick={async (event) => {
                  event.preventDefault()
                  await recreateMissingSchedules({})
                  setDialogOpen(false)
                }}
              >
                {recreateMissingSchedulesIsPending ? "Creating..." : "Create"}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </div>

      <div className="rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Workspace</TableHead>
              <TableHead>Workflow</TableHead>
              <TableHead>Schedule ID</TableHead>
              <TableHead>DB status</TableHead>
              <TableHead>Temporal status</TableHead>
              <TableHead>Last checked</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.length > 0 ? (
              items.map((item) => (
                <TableRow key={item.schedule_id}>
                  <TableCell className="font-medium">
                    {item.workspace_name}
                  </TableCell>
                  <TableCell>
                    {item.workflow_title ?? "Unknown workflow"}
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {formatShortId(item.schedule_id)}
                  </TableCell>
                  <TableCell>
                    <Badge
                      variant={
                        item.db_status === "online" ? "secondary" : "outline"
                      }
                    >
                      {item.db_status}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    <div className="space-y-1">
                      <Badge
                        variant={
                          item.temporal_status === "present"
                            ? "secondary"
                            : "destructive"
                        }
                      >
                        {item.temporal_status}
                      </Badge>
                      {item.error ? (
                        <p className="text-xs text-destructive">{item.error}</p>
                      ) : null}
                    </div>
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {formatTimestamp(item.last_checked_at)}
                  </TableCell>
                </TableRow>
              ))
            ) : (
              <TableRow>
                <TableCell
                  className="h-16 text-center text-sm text-muted-foreground"
                  colSpan={6}
                >
                  No schedules found in this organization.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}
