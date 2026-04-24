"use client"

import { ShieldCheckIcon, ShieldXIcon, TerminalSquareIcon } from "lucide-react"
import Link from "next/link"
import { type ReactNode, useEffect, useState } from "react"
import type {
  SpmAssetClass,
  SpmAssetRead,
  SpmAssetType,
  SpmControlRead,
  SpmEndpointAssetRead,
  SpmEndpointRead,
  SpmFindingRead,
  SpmHarness,
} from "@/client"
import { EntitlementRequiredEmptyState } from "@/components/entitlement-required-empty-state"
import { CenteredSpinner } from "@/components/loading/spinner"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
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
  useSpmEndpointAssets,
  useSpmEndpoints,
  useSpmFindings,
} from "@/hooks/use-spm"
import { getApiErrorDetail } from "@/lib/errors"

const FILTER_INPUT_CLASSES =
  "h-9 w-full rounded-md border border-input bg-transparent px-3 py-2 text-xs focus:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring"

type BadgeVariant = "default" | "secondary" | "destructive" | "outline"
type FindingDecision = "dismiss" | "enforce"

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

function formatLabel(value: string | null | undefined) {
  if (!value) {
    return "Unknown"
  }
  return value.replaceAll("_", " ")
}

function endpointStatusVariant(
  status: SpmEndpointRead["status"]
): BadgeVariant {
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

function findingStatusVariant(status: SpmFindingRead["status"]): BadgeVariant {
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
): BadgeVariant {
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
  children: ReactNode
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

function SpmPageShell(props: {
  action?: ReactNode
  children: ReactNode
  description: string
  title: string
}) {
  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12 py-10">
        <div className="flex w-full items-start justify-between gap-4">
          <div className="space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">
              {props.title}
            </h2>
            <p className="text-base text-muted-foreground">
              {props.description}
            </p>
          </div>
          {props.action}
        </div>
        {props.children}
      </div>
    </div>
  )
}

function getPolicyScope(assetType: string) {
  if (assetType === "claude_md") {
    return {
      description: "Claude local exclusions supported",
      label: "Enforceable",
      variant: "default" as BadgeVariant,
    }
  }
  if (assetType === "agents_md") {
    return {
      description: "Inventory only for Claude",
      label: "Inventory only",
      variant: "outline" as BadgeVariant,
    }
  }
  return null
}

function getObservedState(asset: SpmEndpointAssetRead) {
  if (asset.observed_state?.excluded === true) {
    return {
      detail: "Excluded from Claude instruction-file loading",
      label: "Excluded",
      variant: "default" as BadgeVariant,
    }
  }
  if (asset.observed_state?.disabled === true) {
    return {
      detail: "Disabled locally on the endpoint",
      label: "Disabled",
      variant: "secondary" as BadgeVariant,
    }
  }
  return {
    detail: "Currently observed with no local override",
    label: "Observed",
    variant: "outline" as BadgeVariant,
  }
}

function getFindingEnforcementState(finding: SpmFindingRead) {
  if (finding.status === "enforcement_pending") {
    return {
      label: "Queued",
      value: formatLabel(finding.recommended_action),
      variant: "secondary" as BadgeVariant,
    }
  }
  if (finding.status === "enforced") {
    return {
      label: "Applied",
      value: formatLabel(finding.recommended_action),
      variant: "default" as BadgeVariant,
    }
  }
  if (finding.asset_type === "agents_md") {
    return {
      label: "Inventory only",
      value: "No Claude enforcement path",
      variant: "outline" as BadgeVariant,
    }
  }
  if (finding.recommended_action) {
    return {
      label: "Ready",
      value: formatLabel(finding.recommended_action),
      variant: "outline" as BadgeVariant,
    }
  }
  if (finding.status === "dismissed") {
    return {
      label: "Dismissed",
      value: "No action queued",
      variant: "outline" as BadgeVariant,
    }
  }
  if (finding.status === "resolved") {
    return {
      label: "Resolved",
      value: "No action queued",
      variant: "outline" as BadgeVariant,
    }
  }
  return {
    label: "No action",
    value: "No enforcement payload available",
    variant: "outline" as BadgeVariant,
  }
}

function getComplianceRollup(endpointId: string, findings: SpmFindingRead[]) {
  let dismissed = 0
  let enforced = 0
  let open = 0
  let pending = 0
  let resolved = 0

  for (const finding of findings) {
    if (finding.endpoint_id !== endpointId) {
      continue
    }
    switch (finding.status) {
      case "open":
        open += 1
        break
      case "enforcement_pending":
        pending += 1
        break
      case "enforced":
        enforced += 1
        break
      case "resolved":
        resolved += 1
        break
      case "dismissed":
        dismissed += 1
        break
    }
  }

  if (open > 0) {
    return {
      detail: `${open} open, ${pending} queued, ${enforced + resolved + dismissed} closed`,
      label: "Needs attention",
      variant: "destructive" as BadgeVariant,
    }
  }
  if (pending > 0) {
    return {
      detail: `${pending} queued, ${enforced + resolved + dismissed} closed`,
      label: "Enforcement queued",
      variant: "secondary" as BadgeVariant,
    }
  }
  if (enforced + resolved + dismissed > 0) {
    return {
      detail: `${enforced} enforced, ${resolved} resolved, ${dismissed} dismissed`,
      label: "Compliant",
      variant: "default" as BadgeVariant,
    }
  }
  return {
    detail: "No findings reported yet",
    label: "Unknown",
    variant: "outline" as BadgeVariant,
  }
}

function canCancelPendingEnrollment(endpoint: SpmEndpointRead) {
  return (
    endpoint.status === "pending" &&
    endpoint.enrolled_at == null &&
    endpoint.last_seen_at == null &&
    endpoint.last_sync_at == null
  )
}

function getEndpointName(endpointId: string, endpoints: SpmEndpointRead[]) {
  return (
    endpoints.find((endpoint) => endpoint.id === endpointId)?.name ?? endpointId
  )
}

function getAssetRecord(
  assetId: string,
  assets: SpmAssetRead[]
): SpmAssetRead | undefined {
  return assets.find((asset) => asset.id === assetId)
}

function getAssetPath(
  asset: Pick<SpmAssetRead, "identity_key" | "metadata"> | SpmEndpointAssetRead
) {
  const path =
    typeof asset.metadata?.file_path === "string"
      ? asset.metadata.file_path
      : asset.identity_key
  return path
}

function FilterField(props: { children: ReactNode; label: string }) {
  return (
    <label className="space-y-2">
      <span className="text-xs font-medium text-muted-foreground">
        {props.label}
      </span>
      {props.children}
    </label>
  )
}

function FindingActionButtons(props: {
  busyDecision: { decision: FindingDecision; findingId: string } | null
  finding: SpmFindingRead
  onDecision: (findingId: string, decision: FindingDecision) => Promise<void>
}) {
  const isActive = props.busyDecision?.findingId === props.finding.id
  const canEnforce = props.finding.recommended_action != null

  return (
    <div className="flex justify-end gap-2">
      <Button
        variant="outline"
        size="sm"
        disabled={props.busyDecision != null}
        onClick={() => void props.onDecision(props.finding.id, "dismiss")}
      >
        {isActive && props.busyDecision?.decision === "dismiss"
          ? "Dismissing..."
          : "Dismiss"}
      </Button>
      <Button
        size="sm"
        disabled={props.busyDecision != null || !canEnforce}
        onClick={() => void props.onDecision(props.finding.id, "enforce")}
      >
        {isActive && props.busyDecision?.decision === "enforce"
          ? "Enforcing..."
          : "Enforce"}
      </Button>
    </div>
  )
}

function FindingsTable(props: {
  assets: SpmAssetRead[]
  busyDecision: { decision: FindingDecision; findingId: string } | null
  endpoints: SpmEndpointRead[]
  findings: SpmFindingRead[]
  onDecision?: (findingId: string, decision: FindingDecision) => Promise<void>
  showEndpointColumn?: boolean
}) {
  const showEndpointColumn = props.showEndpointColumn ?? true

  return (
    <div className="rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Summary</TableHead>
            {showEndpointColumn ? <TableHead>Endpoint</TableHead> : null}
            <TableHead>Asset</TableHead>
            <TableHead>Class</TableHead>
            <TableHead>Type</TableHead>
            <TableHead>Control</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Enforcement</TableHead>
            {props.onDecision ? (
              <TableHead className="text-right">Actions</TableHead>
            ) : null}
          </TableRow>
        </TableHeader>
        <TableBody>
          {props.findings.map((finding) => {
            const asset = getAssetRecord(finding.asset_id, props.assets)
            const enforcementState = getFindingEnforcementState(finding)
            return (
              <TableRow key={finding.id}>
                <TableCell>
                  <div className="space-y-1">
                    <div className="font-medium">{finding.summary}</div>
                    <div className="text-xs text-muted-foreground">
                      Severity: {formatLabel(finding.severity)}
                    </div>
                  </div>
                </TableCell>
                {showEndpointColumn ? (
                  <TableCell>
                    <div className="text-sm">
                      {getEndpointName(finding.endpoint_id, props.endpoints)}
                    </div>
                  </TableCell>
                ) : null}
                <TableCell>
                  <div className="space-y-1">
                    <div className="font-medium">
                      {asset?.display_name ?? finding.asset_id}
                    </div>
                    <div className="text-xs text-muted-foreground">
                      {asset ? getAssetPath(asset) : finding.asset_id}
                    </div>
                  </div>
                </TableCell>
                <TableCell>{formatLabel(finding.asset_class)}</TableCell>
                <TableCell>{formatLabel(finding.asset_type)}</TableCell>
                <TableCell>
                  <code className="text-xs">{finding.control_id}</code>
                </TableCell>
                <TableCell>
                  <div className="space-y-2">
                    <Badge variant={findingStatusVariant(finding.status)}>
                      {formatLabel(finding.status)}
                    </Badge>
                  </div>
                </TableCell>
                <TableCell>
                  <div className="space-y-1">
                    <Badge variant={enforcementState.variant}>
                      {enforcementState.label}
                    </Badge>
                    <div className="text-xs text-muted-foreground">
                      {enforcementState.value}
                    </div>
                  </div>
                </TableCell>
                {props.onDecision ? (
                  <TableCell className="text-right">
                    <FindingActionButtons
                      busyDecision={props.busyDecision}
                      finding={finding}
                      onDecision={props.onDecision}
                    />
                  </TableCell>
                ) : null}
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
    </div>
  )
}

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
      <Button onClick={() => setOpen(true)}>Install endpoint</Button>
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

/**
 * Endpoints table plus install drawer.
 */
export function SpmEndpointsView() {
  const { toast } = useToast()
  const { hasEntitlement, isLoading: entitlementLoading } = useEntitlements()
  const { deleteEndpoint } = useSpmActions()
  const endpointsQuery = useSpmEndpoints()
  const findingsQuery = useSpmFindings()
  const [deleteCandidate, setDeleteCandidate] =
    useState<SpmEndpointRead | null>(null)
  const endpoints = endpointsQuery.data?.items ?? []
  const findings = findingsQuery.data?.items ?? []

  async function handleDeleteEndpoint() {
    if (!deleteCandidate) {
      return
    }
    try {
      await deleteEndpoint.mutateAsync({ endpointId: deleteCandidate.id })
      toast({
        title: "Enrollment canceled",
        description: "The pending endpoint enrollment has been removed.",
      })
      setDeleteCandidate(null)
    } catch (error) {
      toast({
        title: "Cancel enrollment failed",
        description:
          getApiErrorDetail(error) ?? "Failed to remove pending enrollment",
        variant: "destructive",
      })
    }
  }

  return renderMaybeLoading(
    entitlementLoading || endpointsQuery.isLoading || findingsQuery.isLoading,
    hasEntitlement("spm"),
    "SPM entitlement required",
    "This organization does not have access to AI SPM yet.",
    <SpmPageShell
      title="Endpoints"
      description="View enrolled endpoints, current sync health, and compliance rollups derived from the latest findings."
      action={<SpmInstallDrawer />}
    >
      {endpoints.length === 0 ? (
        emptyState(
          "No endpoints yet",
          "Create an endpoint enrollment to generate local install commands."
        )
      ) : (
        <div className="rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Compliance</TableHead>
                <TableHead>Last seen</TableHead>
                <TableHead>Last sync</TableHead>
                <TableHead>Sync state</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {endpoints.map((endpoint) => {
                const rollup = getComplianceRollup(endpoint.id, findings)
                const hasSyncError = Boolean(endpoint.last_sync_error)
                return (
                  <TableRow key={endpoint.id}>
                    <TableCell>
                      <div className="space-y-1">
                        <Link
                          href={`/watchtower/endpoints/${endpoint.id}`}
                          className="font-medium underline-offset-4 hover:underline"
                        >
                          {endpoint.name}
                        </Link>
                        <div className="text-xs text-muted-foreground">
                          {formatLabel(endpoint.harness)} on{" "}
                          {formatLabel(endpoint.platform)}
                        </div>
                      </div>
                    </TableCell>
                    <TableCell>
                      <Badge variant={endpointStatusVariant(endpoint.status)}>
                        {formatLabel(endpoint.status)}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <div className="space-y-1">
                        <Badge variant={rollup.variant}>{rollup.label}</Badge>
                        <div className="text-xs text-muted-foreground">
                          {rollup.detail}
                        </div>
                      </div>
                    </TableCell>
                    <TableCell>
                      {formatTimestamp(endpoint.last_seen_at)}
                    </TableCell>
                    <TableCell>
                      {formatTimestamp(endpoint.last_sync_at)}
                    </TableCell>
                    <TableCell>
                      <div className="space-y-1">
                        <Badge
                          variant={hasSyncError ? "destructive" : "outline"}
                        >
                          {hasSyncError ? "Last sync failed" : "No sync error"}
                        </Badge>
                        <div className="max-w-[220px] text-xs text-muted-foreground">
                          {endpoint.last_sync_error ??
                            "Endpoint has not reported a sync error."}
                        </div>
                      </div>
                    </TableCell>
                    <TableCell className="text-right">
                      {canCancelPendingEnrollment(endpoint) ? (
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => setDeleteCandidate(endpoint)}
                        >
                          Cancel enrollment
                        </Button>
                      ) : null}
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </div>
      )}
      <AlertDialog
        open={deleteCandidate != null}
        onOpenChange={(open) => {
          if (!open) {
            setDeleteCandidate(null)
          }
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Cancel pending enrollment</AlertDialogTitle>
            <AlertDialogDescription>
              {deleteCandidate
                ? `Cancel the pending enrollment for ${deleteCandidate.name}? The unused enrollment token will become invalid and this row will be removed from the endpoints list.`
                : "Canceling this pending enrollment invalidates the unused token and removes the row from the endpoints list."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep enrollment</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => void handleDeleteEndpoint()}
              disabled={deleteEndpoint.isPending}
            >
              {deleteEndpoint.isPending
                ? "Canceling enrollment..."
                : "Cancel enrollment"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </SpmPageShell>
  )
}

/**
 * Findings table with dismiss and enforce actions.
 */
export function SpmFindingsView() {
  const { toast } = useToast()
  const { hasEntitlement, isLoading: entitlementLoading } = useEntitlements()
  const assetsQuery = useSpmAssets()
  const endpointsQuery = useSpmEndpoints()
  const findingsQuery = useSpmFindings()
  const { decideFinding } = useSpmActions()
  const [busyDecision, setBusyDecision] = useState<{
    decision: FindingDecision
    findingId: string
  } | null>(null)
  const assets = assetsQuery.data?.items ?? []
  const endpoints = endpointsQuery.data?.items ?? []
  const findings = findingsQuery.data?.items ?? []

  async function handleDecision(findingId: string, decision: FindingDecision) {
    setBusyDecision({ decision, findingId })
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
      toast({
        title: "Finding update failed",
        description: getApiErrorDetail(error) ?? "Failed to update finding",
        variant: "destructive",
      })
    } finally {
      setBusyDecision(null)
    }
  }

  return renderMaybeLoading(
    entitlementLoading ||
      assetsQuery.isLoading ||
      endpointsQuery.isLoading ||
      findingsQuery.isLoading,
    hasEntitlement("spm"),
    "SPM entitlement required",
    "This organization does not have access to AI SPM yet.",
    <SpmPageShell
      title="Findings"
      description="Review control failures, distinguish enforceable Claude assets from inventory-only assets, and queue local enforcement."
    >
      {findings.length === 0 ? (
        emptyState(
          "No findings",
          "Once endpoints sync inventory, control failures will appear here."
        )
      ) : (
        <FindingsTable
          assets={assets}
          busyDecision={busyDecision}
          endpoints={endpoints}
          findings={findings}
          onDecision={handleDecision}
        />
      )}
    </SpmPageShell>
  )
}

/**
 * Assets table for the current SPM catalog.
 */
export function SpmAssetsView() {
  const { hasEntitlement, isLoading: entitlementLoading } = useEntitlements()
  const endpointsQuery = useSpmEndpoints()
  const [selectedAssetClass, setSelectedAssetClass] = useState<
    SpmAssetClass | ""
  >("")
  const [selectedAssetType, setSelectedAssetType] = useState<SpmAssetType | "">(
    ""
  )
  const [selectedEndpointId, setSelectedEndpointId] = useState("")
  const [selectedHarness, setSelectedHarness] = useState<SpmHarness | "">("")
  const assetsQuery = useSpmAssets({
    assetClass: selectedAssetClass || undefined,
    assetType: selectedAssetType || undefined,
    endpointId: selectedEndpointId || undefined,
    harness: selectedHarness || undefined,
  })
  const assets = assetsQuery.data?.items ?? []
  const endpoints = endpointsQuery.data?.items ?? []

  return renderMaybeLoading(
    entitlementLoading || endpointsQuery.isLoading || assetsQuery.isLoading,
    hasEntitlement("spm"),
    "SPM entitlement required",
    "This organization does not have access to AI SPM yet.",
    <SpmPageShell
      title="Assets"
      description="Inspect deduplicated Claude assets and filter them by harness, endpoint, asset class, or asset type."
    >
      <div className="grid gap-4 rounded-lg border p-4 md:grid-cols-4">
        <FilterField label="Harness">
          <select
            aria-label="Filter by harness"
            className={FILTER_INPUT_CLASSES}
            value={selectedHarness}
            onChange={(event) =>
              setSelectedHarness((event.target.value as SpmHarness) || "")
            }
          >
            <option value="">All harnesses</option>
            <option value="claude_code">Claude Code</option>
          </select>
        </FilterField>
        <FilterField label="Endpoint name">
          <select
            aria-label="Filter by endpoint"
            className={FILTER_INPUT_CLASSES}
            value={selectedEndpointId}
            onChange={(event) => setSelectedEndpointId(event.target.value)}
          >
            <option value="">All endpoints</option>
            {endpoints.map((endpoint) => (
              <option key={endpoint.id} value={endpoint.id}>
                {endpoint.name}
              </option>
            ))}
          </select>
        </FilterField>
        <FilterField label="Asset class">
          <select
            aria-label="Filter by asset class"
            className={FILTER_INPUT_CLASSES}
            value={selectedAssetClass}
            onChange={(event) =>
              setSelectedAssetClass((event.target.value as SpmAssetClass) || "")
            }
          >
            <option value="">All classes</option>
            <option value="mcp_server">MCP server</option>
            <option value="skill">Skill</option>
            <option value="instruction_file">Instruction file</option>
            <option value="workspace_access">Workspace access</option>
            <option value="permissions">Permissions</option>
            <option value="sandbox">Sandbox</option>
            <option value="extension">Extension</option>
            <option value="agent">Agent</option>
          </select>
        </FilterField>
        <FilterField label="Asset type">
          <select
            aria-label="Filter by asset type"
            className={FILTER_INPUT_CLASSES}
            value={selectedAssetType}
            onChange={(event) =>
              setSelectedAssetType((event.target.value as SpmAssetType) || "")
            }
          >
            <option value="">All types</option>
            <option value="mcp_server">MCP server</option>
            <option value="skill">Skill</option>
            <option value="claude_md">CLAUDE.md</option>
            <option value="agents_md">AGENTS.md</option>
            <option value="trusted_directory">Trusted directory</option>
            <option value="additional_directory">Additional directory</option>
            <option value="permission_config">Permission config</option>
            <option value="sandbox_config">Sandbox config</option>
            <option value="hook">Hook</option>
            <option value="subagent">Subagent</option>
          </select>
        </FilterField>
      </div>
      {assets.length === 0 ? (
        emptyState(
          "No assets",
          "Endpoint inventory will appear here after a successful sync."
        )
      ) : (
        <div className="rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Harness</TableHead>
                <TableHead>Class</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Policy scope</TableHead>
                <TableHead>Last seen</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {assets.map((asset) => {
                const scope = getPolicyScope(asset.asset_type)
                return (
                  <TableRow key={asset.id}>
                    <TableCell>
                      <div className="space-y-1">
                        <div className="font-medium">{asset.display_name}</div>
                        <div className="text-xs text-muted-foreground">
                          {getAssetPath(asset)}
                        </div>
                      </div>
                    </TableCell>
                    <TableCell>{formatLabel(asset.harness)}</TableCell>
                    <TableCell>{formatLabel(asset.asset_class)}</TableCell>
                    <TableCell>{formatLabel(asset.asset_type)}</TableCell>
                    <TableCell>
                      {scope ? (
                        <div className="space-y-1">
                          <Badge variant={scope.variant}>{scope.label}</Badge>
                          <div className="text-xs text-muted-foreground">
                            {scope.description}
                          </div>
                        </div>
                      ) : (
                        <span className="text-sm text-muted-foreground">
                          Standard inventory
                        </span>
                      )}
                    </TableCell>
                    <TableCell>{formatTimestamp(asset.last_seen_at)}</TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </div>
      )}
    </SpmPageShell>
  )
}

/**
 * Controls table for the static SPM catalog.
 */
export function SpmControlsView() {
  const { hasEntitlement, isLoading: entitlementLoading } = useEntitlements()
  const controlsQuery = useSpmControls()
  const endpointsQuery = useSpmEndpoints()
  const [selectedControlId, setSelectedControlId] = useState<string | null>(
    null
  )
  const controls = controlsQuery.data ?? []
  const activeControlId = selectedControlId ?? controls[0]?.id
  const findingsQuery = useSpmFindings({
    controlId: activeControlId,
  })
  const endpoints = endpointsQuery.data?.items ?? []
  const findings = findingsQuery.data?.items ?? []
  const selectedControl =
    controls.find((control) => control.id === activeControlId) ?? controls[0]

  useEffect(() => {
    if (!selectedControlId && controls[0]) {
      setSelectedControlId(controls[0].id)
      return
    }
    if (
      selectedControlId &&
      !controls.some((control) => control.id === selectedControlId)
    ) {
      setSelectedControlId(controls[0]?.id ?? null)
    }
  }, [controls, selectedControlId])

  const impactedEndpointIds = Array.from(
    new Set(findings.map((finding) => finding.endpoint_id))
  )

  return renderMaybeLoading(
    entitlementLoading ||
      controlsQuery.isLoading ||
      endpointsQuery.isLoading ||
      findingsQuery.isLoading,
    hasEntitlement("spm"),
    "SPM entitlement required",
    "This organization does not have access to AI SPM yet.",
    <SpmPageShell
      title="Controls"
      description="Browse the Claude control catalog and drill into the related endpoints and findings on the same page."
    >
      {controls.length === 0 ? (
        emptyState(
          "No controls",
          "The generated client did not return any SPM controls."
        )
      ) : (
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,0.8fr)]">
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
                {controls.map((control) => {
                  const isSelected = control.id === selectedControl?.id
                  return (
                    <TableRow
                      key={control.id}
                      className={isSelected ? "bg-muted/30" : undefined}
                    >
                      <TableCell>
                        <button
                          type="button"
                          className="w-full text-left"
                          onClick={() => setSelectedControlId(control.id)}
                        >
                          <div className="space-y-1">
                            <div className="font-medium">{control.title}</div>
                            <div className="text-xs text-muted-foreground">
                              <code>{control.id}</code>
                            </div>
                          </div>
                        </button>
                      </TableCell>
                      <TableCell>
                        <Badge variant={severityVariant(control.severity)}>
                          {formatLabel(control.severity)}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        {formatLabel(control.asset_class)} /{" "}
                        {formatLabel(control.asset_type)}
                      </TableCell>
                      <TableCell>{formatLabel(control.action)}</TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          </div>
          <div className="space-y-4">
            {selectedControl ? (
              <>
                <div className="rounded-lg border p-4">
                  <div className="space-y-3">
                    <div>
                      <div className="text-sm font-medium">
                        {selectedControl.title}
                      </div>
                      <div className="mt-1 text-sm text-muted-foreground">
                        {selectedControl.description}
                      </div>
                    </div>
                    <div className="grid gap-3 text-sm md:grid-cols-2">
                      <div>
                        <div className="font-medium">Severity</div>
                        <div className="mt-1">
                          <Badge
                            variant={severityVariant(selectedControl.severity)}
                          >
                            {formatLabel(selectedControl.severity)}
                          </Badge>
                        </div>
                      </div>
                      <div>
                        <div className="font-medium">Action</div>
                        <div className="mt-1 text-muted-foreground">
                          {formatLabel(selectedControl.action)}
                        </div>
                      </div>
                      <div>
                        <div className="font-medium">Harness</div>
                        <div className="mt-1 text-muted-foreground">
                          {formatLabel(selectedControl.harness)}
                        </div>
                      </div>
                      <div>
                        <div className="font-medium">Asset target</div>
                        <div className="mt-1 text-muted-foreground">
                          {formatLabel(selectedControl.asset_class)} /{" "}
                          {formatLabel(selectedControl.asset_type)}
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
                <div className="rounded-lg border p-4">
                  <div className="text-sm font-medium">Impacted endpoints</div>
                  <div className="mt-3 space-y-2">
                    {impactedEndpointIds.length > 0 ? (
                      impactedEndpointIds.map((endpointId) => (
                        <div
                          key={endpointId}
                          className="text-sm text-muted-foreground"
                        >
                          {getEndpointName(endpointId, endpoints)}
                        </div>
                      ))
                    ) : (
                      <div className="text-sm text-muted-foreground">
                        No current findings for this control.
                      </div>
                    )}
                  </div>
                </div>
                <div className="rounded-lg border">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Endpoint</TableHead>
                        <TableHead>Summary</TableHead>
                        <TableHead>Status</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {findings.length > 0 ? (
                        findings.map((finding) => (
                          <TableRow key={finding.id}>
                            <TableCell>
                              {getEndpointName(finding.endpoint_id, endpoints)}
                            </TableCell>
                            <TableCell>{finding.summary}</TableCell>
                            <TableCell>
                              <Badge
                                variant={findingStatusVariant(finding.status)}
                              >
                                {formatLabel(finding.status)}
                              </Badge>
                            </TableCell>
                          </TableRow>
                        ))
                      ) : (
                        <TableRow>
                          <TableCell
                            colSpan={3}
                            className="text-center text-sm text-muted-foreground"
                          >
                            No findings currently match this control.
                          </TableCell>
                        </TableRow>
                      )}
                    </TableBody>
                  </Table>
                </div>
              </>
            ) : null}
          </div>
        </div>
      )}
    </SpmPageShell>
  )
}

/**
 * Endpoint detail page with related assets and findings.
 */
export function SpmEndpointDetailView(props: { endpointId: string }) {
  const { toast } = useToast()
  const { hasEntitlement, isLoading: entitlementLoading } = useEntitlements()
  const endpointQuery = useSpmEndpoint(props.endpointId)
  const endpointAssetsQuery = useSpmEndpointAssets(props.endpointId)
  const endpointsQuery = useSpmEndpoints()
  const findingsQuery = useSpmFindings({ endpointId: props.endpointId })
  const { decideFinding } = useSpmActions()
  const [busyDecision, setBusyDecision] = useState<{
    decision: FindingDecision
    findingId: string
  } | null>(null)
  const endpoint = endpointQuery.data
  const endpointAssets = endpointAssetsQuery.data?.items ?? []
  const findings = findingsQuery.data?.items ?? []
  const dedupedAssets: SpmAssetRead[] = endpointAssets.map((asset) => ({
    id: asset.asset_id,
    organization_id: asset.organization_id,
    harness: asset.harness,
    asset_class: asset.asset_class,
    asset_type: asset.asset_type,
    identity_key: asset.identity_key,
    display_name: asset.display_name,
    content_hash: asset.content_hash,
    metadata: asset.metadata ?? {},
    first_seen_at: asset.first_seen_at,
    last_seen_at: asset.last_seen_at,
    created_at: asset.first_seen_at,
    updated_at: asset.last_seen_at,
  }))

  async function handleDecision(findingId: string, decision: FindingDecision) {
    setBusyDecision({ decision, findingId })
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
      toast({
        title: "Finding update failed",
        description: getApiErrorDetail(error) ?? "Failed to update finding",
        variant: "destructive",
      })
    } finally {
      setBusyDecision(null)
    }
  }

  return renderMaybeLoading(
    entitlementLoading ||
      endpointQuery.isLoading ||
      endpointAssetsQuery.isLoading ||
      endpointsQuery.isLoading ||
      findingsQuery.isLoading,
    hasEntitlement("spm"),
    "SPM entitlement required",
    "This organization does not have access to AI SPM yet.",
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12 py-10">
        <div className="space-y-4">
          <Link
            href="/watchtower/endpoints"
            className="inline-flex items-center gap-2 text-sm text-muted-foreground underline-offset-4 hover:underline"
          >
            <TerminalSquareIcon className="h-4 w-4" />
            Back to endpoints
          </Link>
          <div className="space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">
              {endpoint?.name ?? "Endpoint"}
            </h2>
            <p className="text-base text-muted-foreground">
              Review endpoint metadata, latest sync state, endpoint-scoped
              inventory, and endpoint-specific findings.
            </p>
          </div>
        </div>
        {endpoint ? (
          <>
            <div className="grid gap-4 md:grid-cols-2">
              <div className="rounded-lg border p-4">
                <div className="flex items-center justify-between gap-4">
                  <div className="text-sm font-medium">Endpoint status</div>
                  <Badge variant={endpointStatusVariant(endpoint.status)}>
                    {formatLabel(endpoint.status)}
                  </Badge>
                </div>
                <dl className="mt-4 space-y-2 text-sm">
                  <div className="flex justify-between gap-4">
                    <dt className="text-muted-foreground">Harness</dt>
                    <dd>{formatLabel(endpoint.harness)}</dd>
                  </div>
                  <div className="flex justify-between gap-4">
                    <dt className="text-muted-foreground">Platform</dt>
                    <dd>{formatLabel(endpoint.platform)}</dd>
                  </div>
                  <div className="flex justify-between gap-4">
                    <dt className="text-muted-foreground">Hostname</dt>
                    <dd>{endpoint.hostname ?? "Unknown"}</dd>
                  </div>
                  <div className="flex justify-between gap-4">
                    <dt className="text-muted-foreground">OS user</dt>
                    <dd>{endpoint.os_user ?? "Unknown"}</dd>
                  </div>
                  <div className="flex justify-between gap-4">
                    <dt className="text-muted-foreground">Home path</dt>
                    <dd>{endpoint.home_path ?? "Unknown"}</dd>
                  </div>
                  <div className="flex justify-between gap-4">
                    <dt className="text-muted-foreground">Version</dt>
                    <dd>{endpoint.endpoint_version ?? "Unknown"}</dd>
                  </div>
                </dl>
              </div>
              <div className="rounded-lg border p-4">
                <div className="text-sm font-medium">Latest sync state</div>
                <dl className="mt-4 space-y-2 text-sm">
                  <div className="flex justify-between gap-4">
                    <dt className="text-muted-foreground">Last seen</dt>
                    <dd>{formatTimestamp(endpoint.last_seen_at)}</dd>
                  </div>
                  <div className="flex justify-between gap-4">
                    <dt className="text-muted-foreground">Last sync</dt>
                    <dd>{formatTimestamp(endpoint.last_sync_at)}</dd>
                  </div>
                </dl>
                <div className="mt-4 rounded-md bg-muted p-3 text-xs text-muted-foreground">
                  {endpoint.last_sync_error ?? "No sync error reported."}
                </div>
                <div className="mt-4 text-sm font-medium">Client metadata</div>
                <pre className="mt-2 overflow-x-auto rounded bg-muted p-3 text-xs">
                  {JSON.stringify(endpoint.client_metadata, null, 2)}
                </pre>
              </div>
            </div>
            <section className="space-y-4">
              <h3 className="text-lg font-semibold">Endpoint assets</h3>
              {endpointAssets.length > 0 ? (
                <div className="rounded-lg border">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Name</TableHead>
                        <TableHead>Type</TableHead>
                        <TableHead>Policy scope</TableHead>
                        <TableHead>Observed state</TableHead>
                        <TableHead>Last seen</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {endpointAssets.map((asset) => {
                        const scope = getPolicyScope(asset.asset_type)
                        const observedState = getObservedState(asset)
                        return (
                          <TableRow key={asset.asset_sighting_id}>
                            <TableCell>
                              <div className="space-y-1">
                                <div className="font-medium">
                                  {asset.display_name}
                                </div>
                                <div className="text-xs text-muted-foreground">
                                  {getAssetPath(asset)}
                                </div>
                              </div>
                            </TableCell>
                            <TableCell>
                              {formatLabel(asset.asset_class)} /{" "}
                              {formatLabel(asset.asset_type)}
                            </TableCell>
                            <TableCell>
                              {scope ? (
                                <div className="space-y-1">
                                  <Badge variant={scope.variant}>
                                    {scope.label}
                                  </Badge>
                                  <div className="text-xs text-muted-foreground">
                                    {scope.description}
                                  </div>
                                </div>
                              ) : (
                                <span className="text-sm text-muted-foreground">
                                  Standard inventory
                                </span>
                              )}
                            </TableCell>
                            <TableCell>
                              <div className="space-y-1">
                                <Badge variant={observedState.variant}>
                                  {observedState.label}
                                </Badge>
                                <div className="text-xs text-muted-foreground">
                                  {observedState.detail}
                                </div>
                              </div>
                            </TableCell>
                            <TableCell>
                              {formatTimestamp(asset.last_seen_at)}
                            </TableCell>
                          </TableRow>
                        )
                      })}
                    </TableBody>
                  </Table>
                </div>
              ) : (
                emptyState(
                  "No endpoint assets",
                  "This endpoint has not reported any inventory yet."
                )
              )}
            </section>
            <section className="space-y-4">
              <h3 className="text-lg font-semibold">Endpoint findings</h3>
              {findings.length > 0 ? (
                <FindingsTable
                  assets={dedupedAssets}
                  busyDecision={busyDecision}
                  endpoints={endpointsQuery.data?.items ?? []}
                  findings={findings}
                  onDecision={handleDecision}
                  showEndpointColumn={false}
                />
              ) : (
                emptyState(
                  "No endpoint findings",
                  "No control failures are currently open for this endpoint."
                )
              )}
            </section>
          </>
        ) : (
          emptyState(
            "Endpoint not found",
            "The requested endpoint did not load."
          )
        )}
      </div>
    </div>
  )
}
