import Link from "next/link"
import { Fragment, type ReactNode } from "react"
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb"

export function normalizeFolderPath(folderPath: string | null | undefined) {
  if (!folderPath || folderPath === "/") return "/"
  const pathWithLeadingSlash = folderPath.startsWith("/")
    ? folderPath
    : `/${folderPath}`
  return pathWithLeadingSlash.endsWith("/") && pathWithLeadingSlash !== "/"
    ? pathWithLeadingSlash.slice(0, -1)
    : pathWithLeadingSlash
}

export function getFolderPathHref(baseHref: string, folderPath: string) {
  if (folderPath === "/") return `${baseHref}?view=folders&path=%2F`
  return `${baseHref}?view=folders&path=${encodeURIComponent(folderPath)}`
}

export function FolderPathBreadcrumb({
  rootLabel,
  rootHref,
  folderPath,
  currentPage,
  currentPageFallback,
}: {
  rootLabel: ReactNode
  rootHref: string
  folderPath?: string | null
  currentPage?: ReactNode
  currentPageFallback?: ReactNode
}) {
  const normalizedPath = normalizeFolderPath(folderPath)
  const segments = normalizedPath.split("/").filter(Boolean)
  const hasCurrentPage =
    currentPage !== undefined || currentPageFallback !== undefined

  return (
    <Breadcrumb>
      <BreadcrumbList className="relative z-10 flex items-center gap-2 text-sm flex-nowrap overflow-hidden whitespace-nowrap min-w-0 bg-transparent pr-1">
        <BreadcrumbItem>
          <BreadcrumbLink asChild className="font-semibold hover:no-underline">
            <Link href={rootHref}>{rootLabel}</Link>
          </BreadcrumbLink>
        </BreadcrumbItem>
        {segments.map((segment, index) => {
          const currentFolderPath = `/${segments.slice(0, index + 1).join("/")}`
          const isLastFolder = index === segments.length - 1
          const shouldRenderAsPage = isLastFolder && !hasCurrentPage
          return (
            <Fragment key={currentFolderPath}>
              <BreadcrumbSeparator className="shrink-0">
                <span className="text-muted-foreground">/</span>
              </BreadcrumbSeparator>
              <BreadcrumbItem>
                {shouldRenderAsPage ? (
                  <BreadcrumbPage className="font-semibold">
                    {segment}
                  </BreadcrumbPage>
                ) : (
                  <BreadcrumbLink
                    asChild
                    className="font-semibold hover:no-underline"
                  >
                    <Link href={getFolderPathHref(rootHref, currentFolderPath)}>
                      {segment}
                    </Link>
                  </BreadcrumbLink>
                )}
              </BreadcrumbItem>
            </Fragment>
          )
        })}
        {hasCurrentPage ? (
          <>
            <BreadcrumbSeparator className="shrink-0">
              <span className="text-muted-foreground">/</span>
            </BreadcrumbSeparator>
            <BreadcrumbItem>
              {currentPage !== undefined && currentPage !== null ? (
                <BreadcrumbPage className="font-semibold min-w-0">
                  {currentPage}
                </BreadcrumbPage>
              ) : (
                currentPageFallback
              )}
            </BreadcrumbItem>
          </>
        ) : null}
      </BreadcrumbList>
    </Breadcrumb>
  )
}
