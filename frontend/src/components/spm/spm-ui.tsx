"use client"

import { ShieldCheckIcon, ShieldXIcon, TerminalSquareIcon } from "lucide-react"
import Link from "next/link"
import { useEffect, useState } from "react"
import type { SpmControlRead, SpmEndpointRead, SpmFindingRead } from "@/client"
import { EntitlementRequiredEmptyState } from "@/components/entitlement-required-empty-state"
import { CenteredSpinner } from "@/components/loading/spinner"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { Input } from "@/components/ui/input"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { useToast } from "@/components/ui/use-toast"
import { useEntitlements } from "@/hooks/use-entitlements"
import {
  useSpmActions,
  useSpmAssets,
  useSpmControls,
  useSpmEndpoint,
  useSpmEndpoints,
  useSpmFindings,
} from "@/hooks/use-spm"

function formatTimestamp(value: string | null | undefined) {
  if (!value) {
    return "Never"
  }
  const date = new Date(value)
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date)
}

function endpointStatusVariant(
  status: SpmEndpointRead["status"]
): "default" | "secondary" | "destructive" | "outline" {
  switch (status) {
    case "active":
      return "default"
    case "disabled":
      return "destructive"
    case "pending":
      return "secondary"
    default:
      return "outline"
  }
}

function findingStatusVariant(
  status: SpmFindingRead["status"]
): "default" | "secondary" | "destructive" | "outline" {
  switch (status) {
    case "open":
      return "destructive"
    case "enforcement_pending":
      return "secondary"
    case "enforced":
      return "default"
    case "resolved":
      return "outline"
    default:
      return "secondary"
  }
}

function severityVariant(
  severity: SpmFindingRead["severity"] | SpmControlRead["severity"]
): "default" | "secondary" | "destructive" | "outline" {
  switch (severity) {
    case "critical":
    case "high":
      return "destructive"
    case "medium":
      return "secondary"
    default:
      return "outline"
  }
}

function renderMaybeLoading(
  isLoading: boolean,
  hasEntitlement: boolean,
  title: string,
  description: string,
  children: React.ReactNode
) {
  if (isLoading) {
    return <CenteredSpinner />
  }
  if (!hasEntitlement) {
    return (
      <div className="size-full overflow-auto">
        <div className="container flex h-full max-w-[1000px] flex-col py-10">
          <EntitlementRequiredEmptyState
            title={title}
            description={description}
            icon={<ShieldXIcon className="h-6 w-6" />}
          />
        </div>
      </div>
    )
  }
  return children
}

function emptyState(title: string, description: string) {
  return (
    <Empty className="min-h-[240px] gap-4 border">
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <ShieldCheckIcon className="h-6 w-6" />
        </EmptyMedia>
        <EmptyTitle>{title}</EmptyTitle>
        <EmptyDescription>{description}</EmptyDescription>
      </EmptyHeader>
    </Empty>
  )
}

/**
 * Drawer for creating an endpoint enrollment and showing manual bootstrap commands.
 */
export function SpmInstallDrawer() {
  const { toast } = useToast()
  const { createEndpoint } = useSpmActions()
  const [open, setOpen] = useState(false)
  const [name, setName] = useState("MacBook")
  const [createdEndpoint, setCreatedEndpoint] = useState<{
    endpointId: string
    enrollmentToken: string
  } | null>(null)
  const [origin, setOrigin] = useState("")

  useEffect(() => {
    setOrigin(window.location.origin)
  }, [])

  const handleCreate = async () => {
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
      const message =
        error instanceof Error ? error.message : "Failed to create endpoint"
      toast({
        title: "Create endpoint failed",
        description: message,
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
      <Button onClick={() => setOpen(true)}>Install endpoint</Button>
      <SheetContent side="right" className="w-full sm:max-w-xl">
        <SheetHeader>
          <SheetTitle>Install Tracecat Endpoint</SheetTitle>
          <SheetDescription>
            Create an endpoint enrollment, then bootstrap `tracecatd` on the
            target machine with the returned token.
          </SheetDescription>
        </SheetHeader>
        <div className="mt-8 space-y-6">
          <div className="space-y-2">
            <label htmlFor="endpoint-name" className="text-sm font-medium">
              Endpoint name
            </label>
            <Input
              id="endpoint-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Chris MacBook"
            />
          </div>
          <div className="flex justify-end">
            <Button
              onClick={handleCreate}
              disabled={createEndpoint.isPending || name.trim().length === 0}
            >
              {createEndpoint.isPending ? "Creating..." : "Create endpoint"}
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

/**
 * Endpoints table plus install drawer.
 */
export function SpmEndpointsView() {
  const { hasEntitlement, isLoading: entitlementLoading } = useEntitlements()
  const { data, isLoading } = useSpmEndpoints()
  const endpoints = data?.items ?? []

  return renderMaybeLoading(
    entitlementLoading || isLoading,
    hasEntitlement("spm"),
    "SPM entitlement required",
    "This organization does not have access to AI SPM yet.",
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12 py-10">
        <div className="flex w-full items-start justify-between gap-4">
          <div className="space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">Endpoints</h2>
            <p className="text-base text-muted-foreground">
              View enrolled endpoints, recent sync state, and operator bootstrap
              details.
            </p>
          </div>
          <SpmInstallDrawer />
        </div>
        {endpoints.length === 0 ? (
          emptyState(
            "No endpoints yet",
            "Create an endpoint enrollment to generate install commands."
          )
        ) : (
          <div className="rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Platform</TableHead>
                  <TableHead>Last seen</TableHead>
                  <TableHead>Updated</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {endpoints.map((endpoint) => (
                  <TableRow key={endpoint.id}>
                    <TableCell>
                      <Link
                        href={`/spm/endpoints/${endpoint.id}`}
                        className="font-medium underline-offset-4 hover:underline"
                      >
                        {endpoint.name}
                      </Link>
                    </TableCell>
                    <TableCell>
                      <Badge variant={endpointStatusVariant(endpoint.status)}>
                        {endpoint.status}
                      </Badge>
                    </TableCell>
                    <TableCell>{endpoint.platform}</TableCell>
                    <TableCell>
                      {formatTimestamp(endpoint.last_seen_at)}
                    </TableCell>
                    <TableCell>
                      {formatTimestamp(endpoint.updated_at)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </div>
    </div>
  )
}

/**
 * Findings table with dismiss and enforce actions.
 */
export function SpmFindingsView() {
  const { toast } = useToast()
  const { hasEntitlement, isLoading: entitlementLoading } = useEntitlements()
  const { data, isLoading } = useSpmFindings()
  const { decideFinding } = useSpmActions()
  const findings = data?.items ?? []

  const handleDecision = async (
    findingId: string,
    decision: "dismiss" | "enforce"
  ) => {
    try {
      await decideFinding.mutateAsync({
        findingId,
        requestBody: {
          decision,
          payload: {},
        },
      })
      toast({
        title: "Finding updated",
        description:
          decision === "enforce"
            ? "Enforcement task queued."
            : "Finding dismissed.",
      })
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Failed to update finding"
      toast({
        title: "Finding update failed",
        description: message,
        variant: "destructive",
      })
    }
  }

  return renderMaybeLoading(
    entitlementLoading || isLoading,
    hasEntitlement("spm"),
    "SPM entitlement required",
    "This organization does not have access to AI SPM yet.",
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12 py-10">
        <div className="flex w-full">
          <div className="space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">Findings</h2>
            <p className="text-base text-muted-foreground">
              Review open control failures and drive local enforcement through
              the existing decision flow.
            </p>
          </div>
        </div>
        {findings.length === 0 ? (
          emptyState(
            "No findings",
            "Once endpoints sync inventory, control failures will appear here."
          )
        ) : (
          <div className="rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Summary</TableHead>
                  <TableHead>Severity</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Control</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {findings.map((finding) => (
                  <TableRow key={finding.id}>
                    <TableCell>
                      <div className="space-y-1">
                        <div className="font-medium">{finding.summary}</div>
                        <div className="text-xs text-muted-foreground">
                          {finding.asset_type} on endpoint {finding.endpoint_id}
                        </div>
                      </div>
                    </TableCell>
                    <TableCell>
                      <Badge variant={severityVariant(finding.severity)}>
                        {finding.severity}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <Badge variant={findingStatusVariant(finding.status)}>
                        {finding.status}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <code className="text-xs">{finding.control_id}</code>
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-2">
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={decideFinding.isPending}
                          onClick={() =>
                            void handleDecision(finding.id, "dismiss")
                          }
                        >
                          Dismiss
                        </Button>
                        <Button
                          size="sm"
                          disabled={
                            decideFinding.isPending ||
                            finding.recommended_action == null
                          }
                          onClick={() =>
                            void handleDecision(finding.id, "enforce")
                          }
                        >
                          Enforce
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </div>
    </div>
  )
}

/**
 * Assets table for the current SPM catalog.
 */
export function SpmAssetsView() {
  const { hasEntitlement, isLoading: entitlementLoading } = useEntitlements()
  const { data, isLoading } = useSpmAssets()
  const assets = data?.items ?? []

  return renderMaybeLoading(
    entitlementLoading || isLoading,
    hasEntitlement("spm"),
    "SPM entitlement required",
    "This organization does not have access to AI SPM yet.",
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12 py-10">
        <div className="flex w-full">
          <div className="space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">Assets</h2>
            <p className="text-base text-muted-foreground">
              Inspect the normalized asset catalog produced by endpoint syncs.
            </p>
          </div>
        </div>
        {assets.length === 0 ? (
          emptyState(
            "No assets",
            "Endpoint inventory will appear here after the first successful sync."
          )
        ) : (
          <div className="rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Class</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Last seen</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {assets.map((asset) => (
                  <TableRow key={asset.id}>
                    <TableCell>
                      <div className="space-y-1">
                        <div className="font-medium">{asset.display_name}</div>
                        <div className="text-xs text-muted-foreground">
                          <code>{asset.identity_key}</code>
                        </div>
                      </div>
                    </TableCell>
                    <TableCell>{asset.asset_class}</TableCell>
                    <TableCell>{asset.asset_type}</TableCell>
                    <TableCell>{formatTimestamp(asset.last_seen_at)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </div>
    </div>
  )
}

/**
 * Controls table for the static SPM catalog.
 */
export function SpmControlsView() {
  const { hasEntitlement, isLoading: entitlementLoading } = useEntitlements()
  const { data, isLoading } = useSpmControls()
  const controls = data ?? []

  return renderMaybeLoading(
    entitlementLoading || isLoading,
    hasEntitlement("spm"),
    "SPM entitlement required",
    "This organization does not have access to AI SPM yet.",
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12 py-10">
        <div className="flex w-full">
          <div className="space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">Controls</h2>
            <p className="text-base text-muted-foreground">
              Browse the current Claude Code control catalog and its mapped
              enforcement actions.
            </p>
          </div>
        </div>
        {controls.length === 0 ? (
          emptyState(
            "No controls",
            "The generated client did not return any SPM controls."
          )
        ) : (
          <div className="rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Control</TableHead>
                  <TableHead>Severity</TableHead>
                  <TableHead>Asset</TableHead>
                  <TableHead>Action</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {controls.map((control) => (
                  <TableRow key={control.id}>
                    <TableCell>
                      <div className="space-y-1">
                        <div className="font-medium">{control.title}</div>
                        <div className="text-xs text-muted-foreground">
                          <code>{control.id}</code>
                        </div>
                      </div>
                    </TableCell>
                    <TableCell>
                      <Badge variant={severityVariant(control.severity)}>
                        {control.severity}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      {control.asset_class} / {control.asset_type}
                    </TableCell>
                    <TableCell>{control.action}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </div>
    </div>
  )
}

/**
 * Endpoint detail page with related assets and findings.
 */
export function SpmEndpointDetailView(props: { endpointId: string }) {
  const { hasEntitlement, isLoading: entitlementLoading } = useEntitlements()
  const endpointQuery = useSpmEndpoint(props.endpointId)
  const assetsQuery = useSpmAssets()
  const findingsQuery = useSpmFindings()

  return renderMaybeLoading(
    entitlementLoading ||
      endpointQuery.isLoading ||
      assetsQuery.isLoading ||
      findingsQuery.isLoading,
    hasEntitlement("spm"),
    "SPM entitlement required",
    "This organization does not have access to AI SPM yet.",
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12 py-10">
        <div className="space-y-4">
          <Link
            href="/spm/endpoints"
            className="inline-flex items-center gap-2 text-sm text-muted-foreground underline-offset-4 hover:underline"
          >
            <TerminalSquareIcon className="h-4 w-4" />
            Back to endpoints
          </Link>
          <div className="space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">
              {endpointQuery.data?.name ?? "Endpoint"}
            </h2>
            <p className="text-base text-muted-foreground">
              View current endpoint metadata and related SPM assets and
              findings.
            </p>
          </div>
        </div>
        {endpointQuery.data ? (
          <div className="grid gap-4 md:grid-cols-2">
            <div className="rounded-lg border p-4">
              <div className="text-sm font-medium">Status</div>
              <div className="mt-2">
                <Badge
                  variant={endpointStatusVariant(endpointQuery.data.status)}
                >
                  {endpointQuery.data.status}
                </Badge>
              </div>
              <dl className="mt-4 space-y-2 text-sm">
                <div className="flex justify-between gap-4">
                  <dt className="text-muted-foreground">Hostname</dt>
                  <dd>{endpointQuery.data.hostname ?? "Unknown"}</dd>
                </div>
                <div className="flex justify-between gap-4">
                  <dt className="text-muted-foreground">OS user</dt>
                  <dd>{endpointQuery.data.os_user ?? "Unknown"}</dd>
                </div>
                <div className="flex justify-between gap-4">
                  <dt className="text-muted-foreground">Version</dt>
                  <dd>{endpointQuery.data.endpoint_version ?? "Unknown"}</dd>
                </div>
                <div className="flex justify-between gap-4">
                  <dt className="text-muted-foreground">Last seen</dt>
                  <dd>{formatTimestamp(endpointQuery.data.last_seen_at)}</dd>
                </div>
              </dl>
            </div>
            <div className="rounded-lg border p-4">
              <div className="text-sm font-medium">Client metadata</div>
              <pre className="mt-4 overflow-x-auto rounded bg-muted p-3 text-xs">
                {JSON.stringify(endpointQuery.data.client_metadata, null, 2)}
              </pre>
            </div>
          </div>
        ) : (
          emptyState(
            "Endpoint not found",
            "The requested endpoint did not load."
          )
        )}
        <section className="space-y-4">
          <h3 className="text-lg font-semibold">Related findings</h3>
          <div className="rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Summary</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Severity</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(findingsQuery.data?.items ?? [])
                  .filter((finding) => finding.endpoint_id === props.endpointId)
                  .map((finding) => (
                    <TableRow key={finding.id}>
                      <TableCell>{finding.summary}</TableCell>
                      <TableCell>
                        <Badge variant={findingStatusVariant(finding.status)}>
                          {finding.status}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <Badge variant={severityVariant(finding.severity)}>
                          {finding.severity}
                        </Badge>
                      </TableCell>
                    </TableRow>
                  ))}
              </TableBody>
            </Table>
          </div>
        </section>
        <section className="space-y-4">
          <h3 className="text-lg font-semibold">Recent assets</h3>
          <div className="rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Class</TableHead>
                  <TableHead>Type</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(assetsQuery.data?.items ?? []).slice(0, 12).map((asset) => (
                  <TableRow key={asset.id}>
                    <TableCell>{asset.display_name}</TableCell>
                    <TableCell>{asset.asset_class}</TableCell>
                    <TableCell>{asset.asset_type}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </section>
      </div>
    </div>
  )
}
