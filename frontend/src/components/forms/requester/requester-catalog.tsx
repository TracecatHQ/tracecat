"use client"

import { ChevronRightIcon, ClipboardListIcon } from "lucide-react"
import Link from "next/link"
import type { FormIntakeRead } from "@/client"
import { formStyles, requesterStyles } from "@/components/forms/form-styles"
import {
  RequesterErrorState,
  RequesterState,
} from "@/components/forms/requester/requester-states"
import { CenteredSpinner } from "@/components/loading/spinner"
import { Button } from "@/components/ui/button"
import { useRequesterForms } from "@/hooks/use-requester-forms"
import { cn } from "@/lib/utils"

interface WorkspaceGroup {
  workspaceId: string
  workspaceName: string
  forms: FormIntakeRead[]
}

/** Group forms by workspace, keeping the order the server returned. */
export function groupFormsByWorkspace(
  forms: readonly FormIntakeRead[]
): WorkspaceGroup[] {
  const groups = new Map<string, WorkspaceGroup>()
  for (const form of forms) {
    let group = groups.get(form.workspace_id)
    if (!group) {
      group = {
        workspaceId: form.workspace_id,
        workspaceName: form.workspace_name,
        forms: [],
      }
      groups.set(form.workspace_id, group)
    }
    group.forms.push(form)
  }
  return [...groups.values()]
}

/** The forms a requester can fill in, grouped by workspace. */
export function RequesterCatalog() {
  const {
    forms,
    formsIsLoading,
    formsError,
    hasNextPage,
    isFetchingNextPage,
    fetchNextPage,
    refetch,
  } = useRequesterForms()

  if (formsIsLoading) {
    return <CenteredSpinner />
  }
  if (formsError) {
    return (
      <RequesterErrorState
        title="Couldn't load forms"
        onRetry={() => refetch()}
      />
    )
  }
  if (forms.length === 0) {
    return (
      <RequesterState
        icon={ClipboardListIcon}
        title="No forms yet"
        description="Forms you can fill in will show up here."
      />
    )
  }

  return (
    <div className="flex flex-col gap-6">
      {groupFormsByWorkspace(forms).map((group) => (
        <section key={group.workspaceId}>
          <h2 className="mb-2 text-xs font-medium text-muted-foreground">
            {group.workspaceName}
          </h2>
          <ul className={requesterStyles.list}>
            {group.forms.map((form) => (
              <li key={form.id} className="border-b last:border-b-0">
                <Link
                  href={`/forms/${form.id}`}
                  className="flex items-center gap-3 p-3 hover:bg-muted/50 focus-visible:bg-muted/50 focus-visible:outline-none"
                >
                  <span className="flex size-8 flex-none items-center justify-center rounded-md bg-muted">
                    <ClipboardListIcon className="size-4" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className={cn(formStyles.label, "block truncate")}>
                      {form.title}
                    </span>
                    {form.description && (
                      <span className={cn(formStyles.help, "block truncate")}>
                        {form.description}
                      </span>
                    )}
                  </span>
                  <ChevronRightIcon className="size-4 flex-none text-muted-foreground" />
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ))}
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
