"use client"

import { useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { useToast } from "@/components/ui/use-toast"
import { useSpmActions } from "@/hooks/use-spm"
import { getApiErrorDetail } from "@/lib/errors"

/**
 * Drawer for creating an endpoint enrollment and showing manual bootstrap commands.
 */
export function SpmInstallDrawer() {
  const { toast } = useToast()
  const { createEndpoint } = useSpmActions()
  const [createdEndpoint, setCreatedEndpoint] = useState<{
    endpointId: string
    enrollmentToken: string
  } | null>(null)
  const [name, setName] = useState("MacBook")
  const [open, setOpen] = useState(false)
  const [origin, setOrigin] = useState("")

  useEffect(() => {
    setOrigin(window.location.origin)
  }, [])

  async function handleCreate() {
    try {
      const response = await createEndpoint.mutateAsync({
        name,
        harness: "claude_code",
        platform: "macos",
      })
      setCreatedEndpoint({
        endpointId: response.endpoint.id,
        enrollmentToken: response.enrollment_token,
      })
      toast({
        title: "Endpoint created",
        description: "Manual install commands are ready.",
      })
    } catch (error) {
      toast({
        title: "Create endpoint failed",
        description: getApiErrorDetail(error) ?? "Failed to create endpoint",
        variant: "destructive",
      })
    }
  }

  const installCommand = createdEndpoint
    ? `tracecatd install --server-url ${origin || "https://app.tracecat.com"} --endpoint-id ${createdEndpoint.endpointId} --enrollment-token ${createdEndpoint.enrollmentToken}`
    : ""
  const runOnceCommand = createdEndpoint
    ? `tracecatd run --once --server-url ${origin || "https://app.tracecat.com"} --endpoint-id ${createdEndpoint.endpointId} --enrollment-token ${createdEndpoint.enrollmentToken}`
    : ""

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <button
        type="button"
        className="inline-flex h-7 items-center gap-1.5 rounded-md border border-input bg-transparent px-2 text-xs font-medium transition-colors hover:bg-muted/50"
        onClick={() => setOpen(true)}
      >
        Install endpoint
      </button>
      <SheetContent side="right" className="w-full sm:max-w-xl">
        <SheetHeader>
          <SheetTitle>Install Tracecat Endpoint</SheetTitle>
          <SheetDescription>
            Create an enrollment for a Claude Code macOS endpoint, then run the
            returned `tracecatd` commands on that machine.
          </SheetDescription>
        </SheetHeader>
        <div className="mt-8 space-y-6">
          <div className="grid gap-4 rounded-lg border p-4 text-sm md:grid-cols-2">
            <div>
              <div className="font-medium">Harness</div>
              <div className="mt-1 text-muted-foreground">Claude Code</div>
            </div>
            <div>
              <div className="font-medium">Platform</div>
              <div className="mt-1 text-muted-foreground">macOS</div>
            </div>
          </div>
          <label htmlFor="endpoint-name" className="block space-y-2">
            <span className="text-sm font-medium">Endpoint name</span>
            <Input
              id="endpoint-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Chris MacBook"
            />
          </label>
          <div className="flex justify-end">
            <Button
              onClick={() => void handleCreate()}
              disabled={createEndpoint.isPending || name.trim().length === 0}
            >
              {createEndpoint.isPending
                ? "Creating..."
                : "Create endpoint enrollment"}
            </Button>
          </div>
          {createdEndpoint ? (
            <div className="space-y-4 rounded-lg border p-4">
              <div className="space-y-1">
                <div className="text-sm font-medium">Endpoint ID</div>
                <code className="block rounded bg-muted p-2 text-xs">
                  {createdEndpoint.endpointId}
                </code>
              </div>
              <div className="space-y-1">
                <div className="text-sm font-medium">Enrollment token</div>
                <code className="block rounded bg-muted p-2 text-xs">
                  {createdEndpoint.enrollmentToken}
                </code>
              </div>
              <div className="space-y-2">
                <div className="text-sm font-medium">Install command</div>
                <pre className="overflow-x-auto rounded bg-muted p-3 text-xs">
                  {installCommand}
                </pre>
              </div>
              <div className="space-y-2">
                <div className="text-sm font-medium">Run once command</div>
                <pre className="overflow-x-auto rounded bg-muted p-3 text-xs">
                  {runOnceCommand}
                </pre>
              </div>
            </div>
          ) : null}
        </div>
      </SheetContent>
    </Sheet>
  )
}
