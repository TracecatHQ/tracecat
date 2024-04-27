import React, { Suspense } from "react"

import { fetchLibraryWorkflows } from "@/lib/flow"
import { Skeleton } from "@/components/ui/skeleton"
import { LibraryTile } from "@/components/library/workflow-tile"

export async function Library() {
  const catalogItems = await fetchLibraryWorkflows()

  return (
    <div className="h-full w-full overflow-auto">
      <div className="container flex h-full flex-col  space-y-4 p-16">
        <div className="items-start space-y-8 pt-16 text-left">
          <div className="flex flex-col space-y-2">
            <h2 className="text-2xl font-bold tracking-tight">
              Workflow Library
            </h2>
            <p className="text-md text-muted-foreground">
              Pre-built workflows ready to deploy.
            </p>
          </div>
          <div className="grid grid-cols-4 gap-4">
            {catalogItems ? (
              catalogItems.map((catalogItem, idx) => (
                <LibraryTile key={idx} catalogItem={catalogItem} />
              ))
            ) : (
              <span className="my-4 text-center text-sm text-muted-foreground">
                Catalog is empty.
              </span>
            )}
          </div>
        </div>
        <Suspense
          fallback={
            <div className="flex flex-col gap-2 pt-4">
              <Skeleton className="h-24 w-full" />
              <Skeleton className="h-24 w-full" />
              <Skeleton className="h-24 w-full" />
              <Skeleton className="h-24 w-full" />
            </div>
          }
        ></Suspense>
      </div>
    </div>
  )
}
