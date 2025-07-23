"use client"

import { ChevronLeft, FileTextIcon, LayoutListIcon } from "lucide-react"
import Link from "next/link"
import { useParams, useRouter, useSearchParams } from "next/navigation"
import { useCallback } from "react"
import type { PromptRead } from "@/client"
import { CaseCommentViewer } from "@/components/cases/case-description-editor"
import { JsonViewWithControls } from "@/components/json-viewer"
import { CenteredSpinner } from "@/components/loading/spinner"
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb"
import { Button } from "@/components/ui/button"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useGetPrompt } from "@/hooks/use-prompt"
import { useWorkspace } from "@/providers/workspace"

export default function RunbookDetailPage() {
  const params = useParams()
  const { workspaceId } = useWorkspace()

  if (!params) {
    return <div>Error: Invalid parameters</div>
  }

  const runbookId = params.runbookId as string

  const {
    data: prompt,
    isLoading,
    error,
  } = useGetPrompt({
    workspaceId,
    promptId: runbookId,
  })

  if (isLoading) {
    return <CenteredSpinner />
  }

  if (error) {
    return <div>Error: {error.message}</div>
  }

  if (!prompt) {
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

  return <RunbookDetailContent prompt={prompt} />
}

type RunbookDetailTab = "summary" | "instructions"

function RunbookDetailContent({ prompt }: { prompt: PromptRead }) {
  const { workspaceId } = useWorkspace()
  const router = useRouter()
  const searchParams = useSearchParams()

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
      router.push(`/workspaces/${workspaceId}/runbooks/${prompt.id}?tab=${tab}`)
    },
    [router, workspaceId, prompt.id]
  )

  return (
    <div className="container mx-auto max-w-4xl p-6 min-h-screen">
      {/* Breadcrumb */}
      <Breadcrumb className="mb-6">
        <BreadcrumbList>
          <BreadcrumbItem>
            <BreadcrumbLink asChild>
              <Link href={`/workspaces/${workspaceId}/runbooks`}>Runbooks</Link>
            </BreadcrumbLink>
          </BreadcrumbItem>
          <BreadcrumbSeparator />
          <BreadcrumbItem>
            <BreadcrumbPage>{prompt.title}</BreadcrumbPage>
          </BreadcrumbItem>
        </BreadcrumbList>
      </Breadcrumb>

      {/* Header */}
      <div className="mb-8 flex items-start justify-between">
        <div className="flex items-start gap-4">
          <FileTextIcon className="size-10 p-2 bg-muted rounded-md" />
          <div>
            <h1 className="text-xl font-semibold">{prompt.title}</h1>
            <p className="mt-1 text-muted-foreground">
              Created from chat {prompt.chat_id}
            </p>
            <div className="mt-2 flex gap-2">
              <span className="text-sm text-muted-foreground">
                {prompt.tools.length} tool{prompt.tools.length !== 1 ? "s" : ""}{" "}
                " Created {new Date(prompt.created_at).toLocaleDateString()}
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
            <CaseCommentViewer
              content={prompt.summary || ""}
              className="min-h-[200px]"
            />
          </div>
        </TabsContent>

        <TabsContent value="instructions" className="space-y-6">
          <div className="space-y-4">
            <h3 className="text-lg font-semibold">Raw Prompt (JSON)</h3>
            <JsonViewWithControls
              src={prompt}
              defaultExpanded
              defaultTab="nested"
            />
          </div>
          <div className="space-y-4">
            <h3 className="text-lg font-semibold">Full Prompt Content</h3>
            <pre className="w-full text-xs overflow-x-auto rounded-md border bg-muted-foreground/5 p-4 max-h-[1000px]">
              {prompt.content}
            </pre>
          </div>
        </TabsContent>
      </Tabs>
    </div>
  )
}
