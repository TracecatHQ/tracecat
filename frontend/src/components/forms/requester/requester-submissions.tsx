"use client"

import { format } from "date-fns"
import { InboxIcon } from "lucide-react"
import { formStyles, requesterStyles } from "@/components/forms/form-styles"
import {
  RequesterErrorState,
  RequesterState,
} from "@/components/forms/requester/requester-states"
import { CenteredSpinner } from "@/components/loading/spinner"
import { Button } from "@/components/ui/button"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { useRequesterSubmissions } from "@/hooks/use-requester-forms"
import { cn } from "@/lib/utils"

/**
 * The requester's own submissions: form, reference and time.
 *
 * There is no status column. Requesters never see the workflow run a
 * submission started.
 */
export function RequesterSubmissions() {
  const {
    submissions,
    submissionsIsLoading,
    submissionsError,
    hasNextPage,
    isFetchingNextPage,
    fetchNextPage,
    refetch,
  } = useRequesterSubmissions()

  if (submissionsIsLoading) {
    return <CenteredSpinner />
  }
  if (submissionsError) {
    return (
      <RequesterErrorState
        title="Couldn't load your submissions"
        onRetry={() => refetch()}
      />
    )
  }
  if (submissions.length === 0) {
    return (
      <RequesterState
        icon={InboxIcon}
        title="No submissions yet"
        description="Forms you submit will show up here."
      />
    )
  }

  return (
    <div className="flex flex-col gap-6">
      <div className={cn(requesterStyles.list, "overflow-x-auto")}>
        <Table className="text-sm">
          <TableHeader>
            <TableRow>
              <TableHead>Form</TableHead>
              <TableHead>Reference</TableHead>
              <TableHead>Submitted</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {submissions.map((submission) => (
              <TableRow key={submission.id}>
                <TableCell>
                  <span className="font-medium">{submission.form_title}</span>{" "}
                  <span className={formStyles.help}>
                    {submission.workspace_name}
                  </span>
                </TableCell>
                <TableCell className="font-mono text-[13px]">
                  {submission.reference}
                </TableCell>
                <TableCell className="whitespace-nowrap text-muted-foreground">
                  {format(new Date(submission.submitted_at), "MMM d, yyyy, p")}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      {hasNextPage && (
        <Button
          type="button"
          variant="outline"
          className={cn(formStyles.button, "self-center")}
          disabled={isFetchingNextPage}
          onClick={() => fetchNextPage()}
        >
          {isFetchingNextPage ? "Loading..." : "Load more"}
        </Button>
      )}
    </div>
  )
}
