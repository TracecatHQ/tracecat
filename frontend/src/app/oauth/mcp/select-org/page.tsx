"use client"

import { useRouter } from "next/navigation"
import { Suspense } from "react"
import { CenteredSpinner } from "@/components/loading/spinner"
import { Button } from "@/components/ui/button"

function McpAuthSelectOrgContent() {
  const router = useRouter()

  return (
    <div className="container flex h-full max-w-[600px] flex-col items-center justify-center gap-6 p-16 text-center">
      <div className="flex flex-col gap-2">
        <h2 className="text-2xl font-semibold tracking-tight">
          Platform admin access
        </h2>
        <p className="text-sm text-muted-foreground">
          MCP authorization requires a tenant user account.
        </p>
      </div>
      <Button variant="outline" onClick={() => router.replace("/admin")}>
        Go to admin console
      </Button>
    </div>
  )
}

export default function McpAuthSelectOrgPage() {
  return (
    <Suspense fallback={<CenteredSpinner />}>
      <McpAuthSelectOrgContent />
    </Suspense>
  )
}
