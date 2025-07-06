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
    <div className="no-scrollbar flex h-screen max-h-screen flex-col overflow-hidden">
      <div className="container h-full space-y-6 overflow-auto md:block">
        <div className="flex h-full flex-col space-y-8">
          <div className="no-scrollbar size-full flex-1 overflow-auto">
            <div className="container my-16 space-y-4">
              <Suspense fallback={<CenteredSpinner />}>
                <BreadcrumbNavigation />
              </Suspense>
              <Suspense fallback={<CenteredSpinner />}>{children}</Suspense>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
