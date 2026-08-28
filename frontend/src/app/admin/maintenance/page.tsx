"use client"

import { DatabaseIcon, LoaderCircleIcon } from "lucide-react"
import { useState } from "react"
import {
  adminMaintenanceBackfillCaseAgentSessionInteractions,
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
import { useMutation } from "@/lib/query"

export default function AdminMaintenancePage() {
  const [lastReport, setLastReport] =
    useState<CaseAgentSessionInteractionBackfillResponse>()
  const { mutateAsync: runBackfill, isPending } = useMutation({
    mutationFn: adminMaintenanceBackfillCaseAgentSessionInteractions,
  })

  async function handleRunBackfill() {
    try {
      const report = await runBackfill()
      setLastReport(report)
      toast({
        title: "Backfill complete",
        description: `Created ${report.inserted} case interaction records.`,
      })
    } catch (error) {
      console.error("Failed to backfill case agent interactions", error)
      toast({
        title: "Backfill failed",
        description: "The maintenance operation did not complete.",
        variant: "destructive",
      })
    }
  }

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
            {lastReport ? (
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
                <Button size="sm" disabled={isPending}>
                  {isPending ? (
                    <LoaderCircleIcon className="mr-2 size-4 animate-spin" />
                  ) : null}
                  {isPending ? "Running..." : "Run backfill"}
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
