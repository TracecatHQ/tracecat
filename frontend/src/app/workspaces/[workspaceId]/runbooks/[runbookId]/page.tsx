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
import { JsonViewWithControls } from "@/components/json-viewer"
import { CenteredSpinner } from "@/components/loading/spinner"
import { RunbookInlineAliasEditor } from "@/components/runbooks/runbook-inline-alias-editor"
import { RunbookInstructionsEditor } from "@/components/runbooks/runbook-instructions-editor"
import { RunbookTitleEditor } from "@/components/runbooks/runbook-title-editor"
import { Button } from "@/components/ui/button"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useFeatureFlag } from "@/hooks/use-feature-flags"
import { useGetRunbook, useUpdateRunbook } from "@/hooks/use-runbook"
import { capitalizeFirst } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

export default function RunbookDetailPage() {
  const params = useParams()
  const workspaceId = useWorkspaceId()
  const router = useRouter()
  const { isFeatureEnabled, isLoading: isFeatureLoading } = useFeatureFlag()
  const runbooksEnabled = isFeatureEnabled("runbooks")

  const runbookId = params?.runbookId as string | undefined

  const {
    data: runbook,
    isLoading: isRunbookLoading,
    error,
  } = useGetRunbook({
    workspaceId,
    runbookId: runbookId || "",
    enabled: runbooksEnabled,
  })

  useEffect(() => {
    if (!isFeatureLoading && !runbooksEnabled) {
      router.replace("/not-found")
    }
  }, [isFeatureLoading, runbooksEnabled, router])

  useEffect(() => {
    if (runbooksEnabled && runbook?.title) {
      document.title = runbook.title
    }
  }, [runbooksEnabled, runbook])

  if (!params) {
    return <div>Error: Invalid parameters</div>
  }
  if (isFeatureLoading || !runbooksEnabled) {
    return <CenteredSpinner />
  }

  if (isRunbookLoading) {
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

type RunbookDetailTab = "instructions" | "data"

function RunbookDetailContent({ runbook }: { runbook: RunbookRead }) {
  const workspaceId = useWorkspaceId()
  const router = useRouter()
  const searchParams = useSearchParams()
  const { updateRunbook } = useUpdateRunbook(workspaceId)

  // Get active tab from URL query params, default to "instructions"
  const activeTab = (
    searchParams &&
    ["instructions", "data"].includes(searchParams.get("tab") || "")
      ? (searchParams.get("tab") ?? "instructions")
      : "instructions"
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
          <div className="flex-1 space-y-2">
            <p className="text-muted-foreground">
              {runbook.related_cases?.length
                ? `Linked to ${runbook.related_cases.length} case${runbook.related_cases.length === 1 ? "" : "s"}`
                : "No related cases linked yet"}
            </p>
            <RunbookInlineAliasEditor
              runbookData={runbook}
              updateRunbook={updateRunbook}
            />
            <div className="flex items-center gap-1">
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
            value="instructions"
          >
            <FileTextIcon className="mr-2 size-4" />
            <span>Instructions</span>
          </TabsTrigger>
          <TabsTrigger
            className="flex h-full min-w-24 items-center justify-center rounded-none border-b-2 border-transparent py-0 text-xs data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none"
            value="data"
          >
            <LayoutListIcon className="mr-2 size-4" />
            <span>Data</span>
          </TabsTrigger>
        </TabsList>

        <TabsContent value="instructions" className="space-y-6">
          {/* Main Content */}
          <div className="space-y-6">
            {runbook.instructions !== undefined && (
              <RunbookInstructionsEditor
                runbookData={runbook}
                updateRunbook={updateRunbook}
              />
            )}
          </div>
        </TabsContent>

        <TabsContent value="data" className="space-y-6">
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
