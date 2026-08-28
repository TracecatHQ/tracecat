"use client"

import { DatabaseIcon, LoaderCircleIcon } from "lucide-react"
import { useEffect, useState } from "react"
import {
  adminMaintenanceGetCaseAgentSessionInteractionBackfill,
  adminMaintenanceStartCaseAgentSessionInteractionBackfill,
  type CaseAgentSessionInteractionBackfillResponse,
} from "@/client"
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
import { Button } from "@/components/ui/button"
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemMedia,
  ItemTitle,
} from "@/components/ui/item"
import { toast } from "@/components/ui/use-toast"
import { useMutation, useQuery } from "@/lib/query"

export default function AdminMaintenancePage() {
  const [lastReport, setLastReport] =
    useState<CaseAgentSessionInteractionBackfillResponse>()
  const [operationId, setOperationId] = useState<string>()
  const [reportedOperationId, setReportedOperationId] = useState<string>()
  const { mutateAsync: startBackfill, isPending: isStarting } = useMutation({
    mutationFn: adminMaintenanceStartCaseAgentSessionInteractionBackfill,
  })
  const { data: operation } = useQuery({
    queryKey: ["admin", "maintenance", "case-agent-interactions", operationId],
    queryFn: () =>
      adminMaintenanceGetCaseAgentSessionInteractionBackfill({
        operationId: operationId as string,
      }),
    enabled: operationId !== undefined,
    refetchInterval: (query) =>
      query.state.data?.status === "running" ? 2000 : false,
  })

  useEffect(() => {
    if (!operationId || reportedOperationId === operationId) {
      return
    }
    if (operation?.status === "completed" && operation.report) {
      setLastReport(operation.report)
      setReportedOperationId(operationId)
      toast({
        title: "Backfill complete",
        description: `Created ${operation.report.inserted} case interaction records.`,
      })
    } else if (operation?.status === "failed") {
      setReportedOperationId(operationId)
      toast({
        title: "Backfill failed",
        description: "The maintenance operation did not complete.",
        variant: "destructive",
      })
    }
  }, [operation, operationId, reportedOperationId])

  async function handleRunBackfill() {
    try {
      const started = await startBackfill()
      setOperationId(started.operation_id)
      setLastReport(undefined)
      toast({
        title: "Backfill started",
        description: "The maintenance operation is running in the background.",
      })
    } catch (error) {
      console.error("Failed to start case agent interaction backfill", error)
      toast({
        title: "Could not start backfill",
        description: "The maintenance operation could not be started.",
        variant: "destructive",
      })
    }
  }

  const isRunning =
    isStarting ||
    (operationId !== undefined &&
      operation?.status !== "completed" &&
      operation?.status !== "failed")

  const skippedCount = lastReport
    ? Object.values(lastReport.skipped).reduce(
        (total, count) => total + count,
        0
      )
    : 0

  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">
              Maintenance
            </h2>
            <p className="text-base text-muted-foreground">
              Run platform-wide data maintenance operations.
            </p>
          </div>
        </div>

        <Item variant="outline" className="p-6">
          <ItemMedia variant="icon">
            <DatabaseIcon />
          </ItemMedia>
          <ItemContent>
            <ItemTitle>Case agent interactions</ItemTitle>
            <ItemDescription className="line-clamp-none">
              Backfill successful historical case and comment mutations from
              persisted agent session history. Reads and failed tool calls are
              ignored, and the operation is safe to rerun.
            </ItemDescription>
            {isRunning ? (
              <p className="mt-2 text-sm text-muted-foreground">
                Backfill is running in the background. You can safely leave this
                page.
              </p>
            ) : lastReport ? (
              <p className="mt-2 text-sm text-muted-foreground">
                Last run: {lastReport.inserted} inserted, {lastReport.existing}{" "}
                already present, and {skippedCount} skipped across{" "}
                {lastReport.sessions_scanned} sessions.
              </p>
            ) : null}
          </ItemContent>
          <ItemActions>
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button size="sm" disabled={isRunning}>
                  {isRunning ? (
                    <LoaderCircleIcon className="mr-2 size-4 animate-spin" />
                  ) : null}
                  {isRunning ? "Running..." : "Run backfill"}
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>
                    Run case interaction backfill?
                  </AlertDialogTitle>
                  <AlertDialogDescription>
                    This scans agent session history across every workspace and
                    records successful historical case mutations. It may take
                    several minutes on larger deployments.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Cancel</AlertDialogCancel>
                  <AlertDialogAction onClick={handleRunBackfill}>
                    Run backfill
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </ItemActions>
        </Item>
      </div>
    </div>
  )
}
