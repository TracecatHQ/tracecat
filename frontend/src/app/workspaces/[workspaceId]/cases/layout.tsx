"use client"

import { Suspense } from "react"
import Link from "next/link"
import { usePathname, useSearchParams } from "next/navigation"
import { useWorkspace } from "@/providers/workspace"

import { cn } from "@/lib/utils"
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb"
import { CaseTableInsertButton } from "@/components/cases/case-table-insert-button"
import { CenteredSpinner } from "@/components/loading/spinner"

function BreadcrumbNavigation() {
  const { workspaceId } = useWorkspace()
  const searchParams = useSearchParams()
  const pathname = usePathname()
  const queryCategory = searchParams.get("category")
  const isFieldsPage = pathname.endsWith("/cases/fields")

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
                  queryCategory || isFieldsPage
                    ? "text-muted-foreground"
                    : "text-foreground",
                  !isFieldsPage && "cursor-default text-foreground"
                )}
              >
                <Link href={`/workspaces/${workspaceId}/cases`}>
                  <h2 className="text-2xl font-semibold tracking-tight">
                    Cases
                  </h2>
                </Link>
              </BreadcrumbLink>
            </BreadcrumbItem>
            {isFieldsPage && (
              <>
                <BreadcrumbSeparator>{"/"}</BreadcrumbSeparator>
                <BreadcrumbItem>
                  <BreadcrumbLink>
                    <span className="text-2xl font-semibold tracking-tight text-foreground/80">
                      Custom Fields
                    </span>
                  </BreadcrumbLink>
                </BreadcrumbItem>
              </>
            )}
          </BreadcrumbList>
        </Breadcrumb>
      </div>
      <div className="ml-auto flex items-center space-x-2">
        <CaseTableInsertButton />
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
