"use client"

import { format } from "date-fns"
import { CirclePlusIcon } from "lucide-react"
import Link from "next/link"
import { usePathname, useSearchParams } from "next/navigation"
import { Suspense } from "react"
import { CenteredSpinner } from "@/components/loading/spinner"
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb"
import { Button } from "@/components/ui/button"
import { useCreateCase } from "@/lib/hooks"
import { cn } from "@/lib/utils"
import { useWorkspace } from "@/providers/workspace"

function BreadcrumbNavigation() {
  const { workspaceId } = useWorkspace()
  const searchParams = useSearchParams()
  const pathname = usePathname()
  const queryCategory = searchParams.get("category")
  const { createCase, createCaseIsPending } = useCreateCase(workspaceId)

  // Check if we're on a case detail page
  const pathSegments = pathname.split("/")
  const caseIndex = pathSegments.indexOf("cases")
  const isCaseDetailPage =
    caseIndex !== -1 &&
    pathSegments[caseIndex + 1] &&
    pathSegments[caseIndex + 1].match(/^[a-zA-Z0-9-]+$/)
  const caseId = isCaseDetailPage ? pathSegments[caseIndex + 1] : null

  // Don't render breadcrumb navigation for case detail pages
  if (isCaseDetailPage) {
    return null
  }

  const handleCreateCase = () => {
    createCase({
      summary: `New case - ${format(new Date(), "PPpp")}`,
      description: "",
      status: "unknown",
      priority: "unknown",
      severity: "unknown",
    })
  }

  return (
    <div className="flex w-full items-center justify-between">
      <div className="items-start space-y-3 text-left">
        <Breadcrumb>
          <BreadcrumbList>
            <BreadcrumbItem>
              <BreadcrumbLink
                asChild
                className={cn(
                  "flex items-center",
                  queryCategory || isCaseDetailPage
                    ? "text-muted-foreground"
                    : "text-foreground",
                  !isCaseDetailPage && "cursor-default text-foreground"
                )}
              >
                <Link href={`/workspaces/${workspaceId}/cases`}>
                  <h2 className="text-2xl font-semibold tracking-tight">
                    Cases
                  </h2>
                </Link>
              </BreadcrumbLink>
            </BreadcrumbItem>
            {isCaseDetailPage && caseId && (
              <>
                <BreadcrumbSeparator>{"/"}</BreadcrumbSeparator>
                <BreadcrumbItem>
                  <BreadcrumbLink>
                    <span className="text-2xl font-semibold tracking-tight text-foreground/80">
                      {caseId}
                    </span>
                  </BreadcrumbLink>
                </BreadcrumbItem>
              </>
            )}
          </BreadcrumbList>
        </Breadcrumb>
        <p className="text-md text-muted-foreground">
          View your workspace&apos;s cases here.
        </p>
      </div>
      <div className="ml-auto flex items-center space-x-2">
        {!isCaseDetailPage && (
          <Button
            onClick={handleCreateCase}
            disabled={createCaseIsPending}
            className="h-7 items-center space-x-1 bg-emerald-500/80 px-3 py-1 text-xs text-white shadow-sm hover:border-emerald-500 hover:bg-emerald-400/80"
          >
            <CirclePlusIcon className="size-3" />
            <span>Open case</span>
          </Button>
        )}
      </div>
    </div>
  )
}

export default function CasesLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <div className="size-full overflow-auto">
      <div className="container h-full py-16">
        <div className="flex h-full flex-col space-y-12">
          <Suspense fallback={<CenteredSpinner />}>
            <BreadcrumbNavigation />
          </Suspense>
          <div className="flex-1 overflow-hidden">
            <Suspense fallback={<CenteredSpinner />}>{children}</Suspense>
          </div>
        </div>
      </div>
    </div>
  )
}
