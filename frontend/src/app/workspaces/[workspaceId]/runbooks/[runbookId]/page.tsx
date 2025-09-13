"use client"

import { formatDistanceToNow } from "date-fns/formatDistanceToNow"
import {
  Calendar,
  ChevronLeft,
  FileTextIcon,
  LayoutListIcon,
} from "lucide-react"
import Link from "next/link"
import { useParams, useRouter, useSearchParams } from "next/navigation"
import { useCallback, useEffect } from "react"
import type { RunbookRead } from "@/client"
import { CaseCommentViewer } from "@/components/cases/case-description-editor"
import { JsonViewWithControls } from "@/components/json-viewer"
import { CenteredSpinner } from "@/components/loading/spinner"
import { RunbookSummaryEditor } from "@/components/runbooks/runbook-summary-editor"
import { RunbookTitleEditor } from "@/components/runbooks/runbook-title-editor"
import { Button } from "@/components/ui/button"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useGetRunbook, useUpdateRunbook } from "@/hooks/use-runbook"
import { capitalizeFirst } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

export default function RunbookDetailPage() {
  const params = useParams()
  const workspaceId = useWorkspaceId()

  const runbookId = params?.runbookId as string | undefined

  const {
    data: runbook,
    isLoading,
    error,
  } = useGetRunbook({
    workspaceId,
    runbookId: runbookId || "",
  })

  useEffect(() => {
    if (runbook?.title) {
      document.title = runbook.title
    }
  }, [runbook])

  if (!params) {
    return <div>Error: Invalid parameters</div>
  }
  if (isLoading) {
    return <CenteredSpinner />
  }

  if (error) {
    return <div>Error: {error.message}</div>
  }

  if (!runbook) {
    return (
      <div className="container mx-auto max-w-4xl p-6">
        <div className="flex flex-col items-center justify-center space-y-4 py-12">
          <FileTextIcon className="size-12 text-muted-foreground" />
          <h2 className="text-xl font-semibold">Runbook not found</h2>
          <div className="text-muted-foreground">
            The requested runbook could not be found.
          </div>
          <Link href={`/workspaces/${workspaceId}/runbooks`}>
            <Button variant="outline" className="mt-2">
              <ChevronLeft className="mr-2 size-4" />
              Back to Runbooks
            </Button>
          </Link>
        </div>
      </div>
    )
  }

  return <RunbookDetailContent runbook={runbook} />
}

type RunbookDetailTab = "summary" | "instructions"

function RunbookDetailContent({ runbook }: { runbook: RunbookRead }) {
  const workspaceId = useWorkspaceId()
  const router = useRouter()
  const searchParams = useSearchParams()
  const { updateRunbook } = useUpdateRunbook(workspaceId)

  // Get active tab from URL query params, default to "summary"
  const activeTab = (
    searchParams &&
    ["summary", "instructions"].includes(searchParams.get("tab") || "")
      ? (searchParams.get("tab") ?? "summary")
      : "summary"
  ) as RunbookDetailTab

  // Function to handle tab changes and update URL
  const handleTabChange = useCallback(
    (tab: string) => {
      router.push(
        `/workspaces/${workspaceId}/runbooks/${runbook.id}?tab=${tab}`
      )
    },
    [router, workspaceId, runbook.id]
  )

  return (
    <div className="container mx-auto max-w-4xl p-6 mt-10 min-h-screen">
      {/* Header */}
      <div className="mb-8">
        <RunbookTitleEditor
          runbookData={runbook}
          updateRunbook={updateRunbook}
        />
        <div className="mt-4 flex items-start gap-4">
          <FileTextIcon className="size-10 p-2 bg-muted rounded-md" />
          <div className="flex-1">
            <p className="mt-1 text-muted-foreground">
              {runbook.meta?.case_slug
                ? `Created from ${runbook.meta.case_slug}`
                : runbook.meta?.chat_id
                  ? `Created from chat ${runbook.meta.chat_id}`
                  : "Created manually"}
            </p>
            <div className="mt-2 flex items-center gap-1">
              <Calendar className="size-3 text-muted-foreground" />
              <span className="text-sm text-muted-foreground">
                {capitalizeFirst(
                  formatDistanceToNow(new Date(runbook.created_at), {
                    addSuffix: true,
                  })
                )}
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* Tabs */}
      <Tabs
        value={activeTab}
        onValueChange={handleTabChange}
        className="space-y-6"
      >
        <TabsList className="h-8 justify-start rounded-none bg-transparent p-0 border-b border-border w-full">
          <TabsTrigger
            className="flex h-full min-w-24 items-center justify-center rounded-none border-b-2 border-transparent py-0 text-xs data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none"
            value="summary"
          >
            <LayoutListIcon className="mr-2 size-4" />
            <span>Summary</span>
          </TabsTrigger>
          <TabsTrigger
            className="flex h-full min-w-24 items-center justify-center rounded-none border-b-2 border-transparent py-0 text-xs data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none"
            value="instructions"
          >
            <FileTextIcon className="mr-2 size-4" />
            <span>Instructions</span>
          </TabsTrigger>
        </TabsList>

        <TabsContent value="summary" className="space-y-6">
          {/* Main Content */}
          <div className="space-y-6">
            {runbook.summary !== undefined ? (
              <RunbookSummaryEditor
                runbookData={runbook}
                updateRunbook={updateRunbook}
              />
            ) : (
              <CaseCommentViewer content="" className="min-h-[200px]" />
            )}
          </div>
        </TabsContent>

        <TabsContent value="instructions" className="space-y-6">
          <div className="space-y-4">
            <h3 className="text-lg font-semibold">Payload</h3>
            <JsonViewWithControls
              src={runbook}
              defaultExpanded
              defaultTab="nested"
            />
          </div>
        </TabsContent>
      </Tabs>
    </div>
  )
}
