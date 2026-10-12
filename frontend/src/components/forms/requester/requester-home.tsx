"use client"

import Link from "next/link"
import { useSearchParams } from "next/navigation"
import { MY_SUBMISSIONS_HREF } from "@/components/forms/form-renderer"
import { requesterStyles } from "@/components/forms/form-styles"
import { RequesterCatalog } from "@/components/forms/requester/requester-catalog"
import { RequesterPage } from "@/components/forms/requester/requester-shell"
import { RequesterSubmissions } from "@/components/forms/requester/requester-submissions"
import { cn } from "@/lib/utils"

const TABS = [
  { value: "forms", label: "Forms", href: "/forms" },
  { value: "submissions", label: "My submissions", href: MY_SUBMISSIONS_HREF },
] as const

/**
 * The requester landing page: the catalog of forms and, under
 * `?tab=submissions`, the requester's own submissions.
 *
 * Reads the search params, so render it inside a `Suspense` boundary.
 */
export function RequesterHome() {
  const activeTab =
    useSearchParams().get("tab") === "submissions" ? "submissions" : "forms"

  return (
    <RequesterPage title="Forms">
      <div className={requesterStyles.catalogColumn}>
        <h1 className={requesterStyles.pageTitle}>Forms</h1>
        <nav aria-label="Forms views" className="mt-5 flex gap-5 border-b">
          {TABS.map((tab) => {
            const active = tab.value === activeTab
            return (
              <Link
                key={tab.value}
                href={tab.href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "-mb-px flex h-9 items-center border-b-2 text-sm font-medium",
                  active
                    ? "border-primary text-foreground"
                    : "border-transparent text-muted-foreground hover:text-foreground"
                )}
              >
                {tab.label}
              </Link>
            )
          })}
        </nav>
        <div className="mt-6">
          {activeTab === "forms" ? (
            <RequesterCatalog />
          ) : (
            <RequesterSubmissions />
          )}
        </div>
      </div>
    </RequesterPage>
  )
}
