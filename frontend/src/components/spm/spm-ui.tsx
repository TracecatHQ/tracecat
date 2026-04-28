"use client"

import {
  AlertTriangleIcon,
  CheckCircleIcon,
  CircleDotIcon,
  CirclePauseIcon,
  ClockArrowUpIcon,
  ComputerIcon,
  FileSearchIcon,
  FlagTriangleRightIcon,
  LaptopIcon,
  LayersIcon,
  type LucideIcon,
  PackageIcon,
  RadarIcon,
  ShieldAlertIcon,
  ShieldCheckIcon,
  SignalHighIcon,
  SignalIcon,
  SignalMediumIcon,
  TagIcon,
  WrenchIcon,
} from "lucide-react"
import {
  type ComponentType,
  type ReactNode,
  useDeferredValue,
  useState,
} from "react"
import type {
  SpmAssetClass,
  SpmAssetRead,
  SpmAssetType,
  SpmControlRead,
  SpmEndpointRead,
  SpmEndpointStatus,
  SpmFindingRead,
  SpmFindingStatus,
  SpmHarness,
  SpmSeverity,
} from "@/client"
import { Spinner } from "@/components/loading/spinner"
import { Alert, AlertDescription } from "@/components/ui/alert"
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
import { useToast } from "@/components/ui/use-toast"
import { useEntitlements } from "@/hooks/use-entitlements"
import {
  useSpmActions,
  useSpmAssets,
  useSpmControls,
  useSpmEndpoints,
  useSpmFindings,
} from "@/hooks/use-spm"
import { getApiErrorDetail } from "@/lib/errors"
import { cn } from "@/lib/utils"
import {
  ALL_VALUE,
  canCancelPendingEnrollment,
  EMPTY_FILTERS,
  type FindingDecision,
  formatLabel,
  getAssetPath,
  getAssetRecord,
  getEndpointName,
  getFindingEnforcementState,
  getPolicyScope,
  includesQuery,
  renderMaybeLoading,
} from "./spm-common"
import {
  ASSET_CLASS_OPTIONS,
  ASSET_TYPE_OPTIONS,
  COMPLIANCE_OPTIONS,
  controlOptions,
  ENDPOINT_STATUS_OPTIONS,
  type EndpointComplianceKey,
  endpointComplianceStyles,
  endpointOptions,
  endpointStatusStyles,
  FINDING_STATUS_OPTIONS,
  FilterSelect,
  HARNESS_OPTIONS,
  MultiFilterSelect,
  ResetFiltersButton,
  SEVERITY_OPTIONS,
} from "./spm-filters"
import { FindingActionButtons } from "./spm-findings"
import { SpmInstallDrawer } from "./spm-install-drawer"
import {
  SmallBadge,
  SpmAccordion,
  SpmCompactRow,
  SpmEmptyState,
  SpmListShell,
  SpmSeenAtIcon,
  SpmTimestamp,
  SpmUpdatedAtIcon,
} from "./spm-layout"

export { SpmInstallDrawer } from "./spm-install-drawer"

type FilterMode = "include" | "exclude"
type SelectOption<TValue extends string> = {
  label: string
  value: TValue
}

const SPM_HARNESS_ORDER: SpmHarness[] = ["claude_code"]
const FINDING_STATUS_ORDER: SpmFindingStatus[] = [
  "open",
  "enforcement_pending",
  "enforced",
  "resolved",
  "dismissed",
]
const SEVERITY_ORDER: SpmSeverity[] = ["critical", "high", "medium", "low"]

const severityStyles: Record<
  SpmSeverity,
  { className: string; icon: LucideIcon; label: string }
> = {
  critical: {
    label: "Critical",
    icon: AlertTriangleIcon,
    className: "bg-fuchsia-500/10 text-fuchsia-700",
  },
  high: {
    label: "High",
    icon: SignalIcon,
    className: "bg-red-500/10 text-red-700",
  },
  medium: {
    label: "Medium",
    icon: SignalHighIcon,
    className: "bg-orange-500/10 text-orange-700",
  },
  low: {
    label: "Low",
    icon: SignalMediumIcon,
    className: "bg-yellow-500/10 text-yellow-700",
  },
}

const findingStatusStyles: Record<
  SpmFindingStatus,
  {
    className: string
    icon: LucideIcon
    iconClassName: string
    label: string
    triggerClassName: string
  }
> = {
  open: {
    label: "Open",
    icon: FlagTriangleRightIcon,
    iconClassName: "text-yellow-600",
    className: "bg-yellow-500/10 text-yellow-700",
    triggerClassName:
      "data-[state=open]:border-l-yellow-600 data-[state=open]:bg-yellow-600/[0.03] dark:data-[state=open]:bg-yellow-600/[0.08]",
  },
  enforcement_pending: {
    label: "Enforcement pending",
    icon: ClockArrowUpIcon,
    iconClassName: "text-blue-600",
    className: "bg-blue-500/10 text-blue-700",
    triggerClassName:
      "data-[state=open]:border-l-blue-600 data-[state=open]:bg-blue-600/[0.03] dark:data-[state=open]:bg-blue-600/[0.08]",
  },
  enforced: {
    label: "Enforced",
    icon: CheckCircleIcon,
    iconClassName: "text-green-600",
    className: "bg-green-500/10 text-green-700",
    triggerClassName:
      "data-[state=open]:border-l-green-600 data-[state=open]:bg-green-600/[0.03] dark:data-[state=open]:bg-green-600/[0.08]",
  },
  resolved: {
    label: "Resolved",
    icon: CheckCircleIcon,
    iconClassName: "text-violet-600",
    className: "bg-violet-500/10 text-violet-700",
    triggerClassName:
      "data-[state=open]:border-l-violet-600 data-[state=open]:bg-violet-600/[0.03] dark:data-[state=open]:bg-violet-600/[0.08]",
  },
  dismissed: {
    label: "Dismissed",
    icon: CirclePauseIcon,
    iconClassName: "text-orange-600",
    className: "bg-orange-500/10 text-orange-700",
    triggerClassName:
      "data-[state=open]:border-l-orange-600 data-[state=open]:bg-orange-600/[0.03] dark:data-[state=open]:bg-orange-600/[0.08]",
  },
}

function firstCharacterUppercase(value: string): string {
  if (value.length === 0) return value
  return value.charAt(0).toUpperCase() + value.slice(1)
}

function ColorBadge(props: {
  children: ReactNode
  className?: string
  icon?: ComponentType<{ className?: string }>
}) {
  const Icon = props.icon
  return (
    <Badge
      variant="outline"
      className={cn(
        "h-5 max-w-[220px] items-center gap-1 border-0 px-2 text-[10px] font-normal leading-tight",
        props.className
      )}
    >
      {Icon ? <Icon className="size-3 shrink-0" /> : null}
      <span className="truncate">{props.children}</span>
    </Badge>
  )
}

function SeverityBadge({ severity }: { severity: SpmSeverity }) {
  const config = severityStyles[severity]
  return (
    <ColorBadge icon={config.icon} className={config.className}>
      {config.label}
    </ColorBadge>
  )
}

function FindingStatusBadge({ status }: { status: SpmFindingStatus }) {
  const config = findingStatusStyles[status]
  return (
    <ColorBadge icon={config.icon} className={config.className}>
      {config.label}
    </ColorBadge>
  )
}

function harnessLabel(harness: SpmHarness) {
  if (harness === "claude_code") return "Claude Code"
  return formatLabel(harness)
}

function harnessTriggerClassName(harness: SpmHarness) {
  if (harness === "claude_code") {
    return "data-[state=open]:border-l-amber-600 data-[state=open]:bg-amber-600/[0.03] dark:data-[state=open]:bg-amber-600/[0.08]"
  }
  return "data-[state=open]:border-l-muted-foreground data-[state=open]:bg-muted/50"
}

function groupByHarness<TItem extends { harness: SpmHarness }>(items: TItem[]) {
  const grouped = new Map<SpmHarness, TItem[]>()
  for (const item of items) {
    const next = grouped.get(item.harness) ?? []
    next.push(item)
    grouped.set(item.harness, next)
  }
  return SPM_HARNESS_ORDER.map((harness) => ({
    harness,
    items: grouped.get(harness) ?? [],
  })).filter((group) => group.items.length > 0)
}

function withoutAll<TValue extends string>(
  options: ReadonlyArray<SelectOption<TValue | typeof ALL_VALUE>>
): SelectOption<TValue>[] {
  return options.filter(
    (option): option is SelectOption<TValue> => option.value !== ALL_VALUE
  )
}

function EndpointStatusBadge({ status }: { status: SpmEndpointStatus }) {
  const config = endpointStatusStyles[status]
  return (
    <ColorBadge icon={config.icon} className={config.badgeClassName}>
      {config.label}
    </ColorBadge>
  )
}

function EndpointComplianceBadge({
  complianceKey,
}: {
  complianceKey: EndpointComplianceKey
}) {
  const config = endpointComplianceStyles[complianceKey]
  return (
    <ColorBadge icon={config.icon} className={config.badgeClassName}>
      {config.label}
    </ColorBadge>
  )
}

function EndpointRow({
  endpoint,
  complianceKey,
  onCancelEnrollment,
}: {
  endpoint: SpmEndpointRead
  complianceKey: EndpointComplianceKey
  onCancelEnrollment: () => void
}) {
  return (
    <SpmCompactRow
      icon={<ComputerIcon className="size-4 text-muted-foreground" />}
      title={firstCharacterUppercase(endpoint.name)}
      badges={
        <>
          <EndpointStatusBadge status={endpoint.status} />
          <EndpointComplianceBadge complianceKey={complianceKey} />
        </>
      }
      meta={
        <>
          <SmallBadge icon={LaptopIcon}>macOS</SmallBadge>
          <SmallBadge icon={RadarIcon}>
            {harnessLabel(endpoint.harness)}
          </SmallBadge>
          {endpoint.endpoint_version ? (
            <SmallBadge icon={TagIcon}>{endpoint.endpoint_version}</SmallBadge>
          ) : null}
          <SpmTimestamp
            label="Seen"
            value={endpoint.last_seen_at}
            icon={SpmSeenAtIcon}
          />
          <SpmTimestamp
            label="Synced"
            value={endpoint.last_sync_at}
            icon={SpmUpdatedAtIcon}
          />
        </>
      }
      actions={
        canCancelPendingEnrollment(endpoint) ? (
          <Button
            variant="outline"
            size="sm"
            className="h-7 px-2 text-xs"
            onClick={onCancelEnrollment}
          >
            Cancel enrollment
          </Button>
        ) : null
      }
    />
  )
}

function AssetRow({ asset }: { asset: SpmAssetRead }) {
  const scope = getPolicyScope(asset.asset_type)
  return (
    <SpmCompactRow
      icon={<PackageIcon className="size-4 text-muted-foreground" />}
      title={asset.display_name}
      badges={
        <>
          <SmallBadge>{harnessLabel(asset.harness)}</SmallBadge>
          <SmallBadge>{formatLabel(asset.asset_class)}</SmallBadge>
          <SmallBadge>{formatLabel(asset.asset_type)}</SmallBadge>
          <SmallBadge variant={scope.variant}>{scope.label}</SmallBadge>
        </>
      }
      meta={
        <SpmTimestamp
          label="Seen"
          value={asset.last_seen_at}
          icon={SpmSeenAtIcon}
        />
      }
    />
  )
}

function FindingRow(props: {
  assets: SpmAssetRead[]
  busyDecision: { decision: FindingDecision; findingId: string } | null
  endpoints: SpmEndpointRead[]
  finding: SpmFindingRead
  onDecision: (findingId: string, decision: FindingDecision) => Promise<void>
}) {
  const asset = getAssetRecord(props.finding.asset_id, props.assets)
  const enforcementState = getFindingEnforcementState(props.finding)
  return (
    <SpmCompactRow
      icon={<ShieldAlertIcon className="size-4 text-muted-foreground" />}
      title={
        <>
          <span className="shrink-0 text-xs text-muted-foreground">
            {props.finding.control_key}
          </span>
          <span className="truncate text-xs">{props.finding.summary}</span>
        </>
      }
      badges={
        <>
          <SeverityBadge severity={props.finding.severity} />
          <FindingStatusBadge status={props.finding.status} />
          <SmallBadge variant={enforcementState.variant}>
            {enforcementState.label}
          </SmallBadge>
          <SmallBadge>{formatLabel(props.finding.asset_class)}</SmallBadge>
          <SmallBadge>
            {asset?.display_name ?? props.finding.asset_id}
          </SmallBadge>
          <SmallBadge>
            {getEndpointName(props.finding.endpoint_id, props.endpoints)}
          </SmallBadge>
        </>
      }
      meta={
        <SpmTimestamp
          label="Updated"
          value={props.finding.updated_at}
          icon={SpmUpdatedAtIcon}
        />
      }
      actions={
        <FindingActionButtons
          busyDecision={props.busyDecision}
          finding={props.finding}
          onDecision={props.onDecision}
        />
      }
    />
  )
}

function ControlRow({ control }: { control: SpmControlRead }) {
  return (
    <SpmCompactRow
      icon={<FileSearchIcon className="size-4 text-muted-foreground" />}
      title={
        <>
          <span className="shrink-0 text-xs text-muted-foreground">
            {control.key}
          </span>
          <span className="truncate text-xs">{control.title}</span>
        </>
      }
      badges={
        <>
          <SeverityBadge severity={control.severity} />
          <SmallBadge>{formatLabel(control.asset_class)}</SmallBadge>
          <SmallBadge>{formatLabel(control.asset_type)}</SmallBadge>
          <SmallBadge icon={WrenchIcon}>
            {formatLabel(control.action)}
          </SmallBadge>
        </>
      }
    />
  )
}

/**
 * Endpoints feed plus install drawer.
 */
export function SpmEndpointsView() {
  const { toast } = useToast()
  const { hasEntitlement, isLoading: entitlementLoading } = useEntitlements()
  const { deleteEndpoint } = useSpmActions()
  const endpointsQuery = useSpmEndpoints()
  const findingsQuery = useSpmFindings()
  const [deleteCandidate, setDeleteCandidate] =
    useState<SpmEndpointRead | null>(null)
  const [searchQuery, setSearchQuery] = useState("")
  const deferredSearchQuery = useDeferredValue(searchQuery)
  const [statusFilter, setStatusFilter] = useState<
    SpmEndpointStatus | typeof ALL_VALUE
  >(ALL_VALUE)
  const [complianceFilter, setComplianceFilter] = useState<
    EndpointComplianceKey | typeof ALL_VALUE
  >(ALL_VALUE)
  const endpoints = endpointsQuery.data?.items ?? []
  const findings = findingsQuery.data?.items ?? []
  const endpointsLoaded = endpointsQuery.data != null
  const endpointsArePending = endpointsQuery.isLoading && !endpointsLoaded
  const endpointsUnavailable = endpointsQuery.isError && !endpointsLoaded
  const endpointsErrorDetail = endpointsUnavailable
    ? (getApiErrorDetail(endpointsQuery.error) ?? "Unable to load endpoints.")
    : null
  const findingsUnavailable =
    findingsQuery.isError && findingsQuery.data == null
  const findingsErrorDetail = findingsUnavailable
    ? (getApiErrorDetail(findingsQuery.error) ?? "Unable to load findings.")
    : null

  function endpointComplianceKey(endpointId: string): EndpointComplianceKey {
    const endpointFindings = findings.filter(
      (finding) => finding.endpoint_id === endpointId
    )
    if (endpointFindings.some((finding) => finding.status === "open")) {
      return "needs_attention"
    }
    if (
      endpointFindings.some(
        (finding) => finding.status === "enforcement_pending"
      )
    ) {
      return "enforcement_queued"
    }
    if (endpointFindings.length > 0) {
      return "compliant"
    }
    return "unknown"
  }

  async function handleDeleteEndpoint() {
    if (!deleteCandidate) return
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

  function resetFilters() {
    setSearchQuery("")
    setStatusFilter(ALL_VALUE)
    setComplianceFilter(ALL_VALUE)
  }

  const filteredEndpoints = endpoints.filter((endpoint) => {
    const complianceKey = endpointComplianceKey(endpoint.id)
    return (
      includesQuery(
        [
          endpoint.name,
          endpoint.hostname,
          endpoint.os_user,
          endpoint.endpoint_version,
          endpoint.status,
          endpoint.harness,
          endpoint.platform,
        ],
        deferredSearchQuery
      ) &&
      (statusFilter === ALL_VALUE || endpoint.status === statusFilter) &&
      (complianceFilter === ALL_VALUE || complianceKey === complianceFilter)
    )
  })

  const hasFilters =
    searchQuery.trim().length > 0 ||
    statusFilter !== ALL_VALUE ||
    complianceFilter !== ALL_VALUE

  return renderMaybeLoading(
    entitlementLoading,
    hasEntitlement("spm"),
    "SPM entitlement required",
    "This organization does not have access to AI SPM yet.",
    <SpmListShell
      title="Endpoints"
      icon={ComputerIcon}
      action={<SpmInstallDrawer />}
      searchQuery={searchQuery}
      onSearchChange={setSearchQuery}
      searchPlaceholder="Search endpoints..."
      count={filteredEndpoints.length}
      countLabel="endpoints"
      hasFilters={hasFilters}
      resetButton={<ResetFiltersButton onClick={resetFilters} />}
      filters={
        <>
          <FilterSelect
            label="Status"
            icon={CircleDotIcon}
            value={statusFilter}
            options={ENDPOINT_STATUS_OPTIONS}
            onChange={setStatusFilter}
          />
          <FilterSelect
            label="Compliance"
            icon={ShieldCheckIcon}
            value={complianceFilter}
            options={COMPLIANCE_OPTIONS}
            onChange={setComplianceFilter}
          />
        </>
      }
    >
      {findingsUnavailable ? (
        <div className="border-b p-3">
          <Alert variant="destructive" className="py-3">
            <AlertTriangleIcon className="size-4" />
            <AlertDescription className="text-xs">
              Findings are unavailable. {findingsErrorDetail}
            </AlertDescription>
          </Alert>
        </div>
      ) : null}
      {endpointsArePending ? (
        <div className="flex h-full min-h-[260px] items-center justify-center">
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Spinner className="size-4" />
            <span>Loading endpoints...</span>
          </div>
        </div>
      ) : null}
      {endpointsUnavailable ? (
        <div className="p-3">
          <Alert variant="destructive" className="py-3">
            <AlertTriangleIcon className="size-4" />
            <AlertDescription className="text-xs">
              Endpoints are unavailable. {endpointsErrorDetail}
            </AlertDescription>
          </Alert>
        </div>
      ) : null}
      {!endpointsArePending &&
      !endpointsUnavailable &&
      filteredEndpoints.length === 0 ? (
        <SpmEmptyState
          title={endpoints.length === 0 ? "No endpoints yet" : EMPTY_FILTERS}
          description={
            endpoints.length === 0
              ? "Create an endpoint enrollment to generate local install commands."
              : "Adjust search or filters to find another endpoint."
          }
          icon={<ComputerIcon className="h-6 w-6" />}
        />
      ) : null}
      {!endpointsArePending &&
      !endpointsUnavailable &&
      filteredEndpoints.length > 0 ? (
        <div className="ml-[18px] divide-y divide-border/50">
          {filteredEndpoints.map((endpoint) => (
            <EndpointRow
              key={endpoint.id}
              endpoint={endpoint}
              complianceKey={endpointComplianceKey(endpoint.id)}
              onCancelEnrollment={() => setDeleteCandidate(endpoint)}
            />
          ))}
        </div>
      ) : null}
      <AlertDialog
        open={deleteCandidate != null}
        onOpenChange={(open) => {
          if (!open) setDeleteCandidate(null)
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
    </SpmListShell>
  )
}

/**
 * Findings feed with dismiss and enforce actions.
 */
export function SpmFindingsView() {
  const { toast } = useToast()
  const { hasEntitlement, isLoading: entitlementLoading } = useEntitlements()
  const assetsQuery = useSpmAssets()
  const controlsQuery = useSpmControls()
  const endpointsQuery = useSpmEndpoints()
  const { decideFinding } = useSpmActions()
  const [busyDecision, setBusyDecision] = useState<{
    decision: FindingDecision
    findingId: string
  } | null>(null)
  const [searchQuery, setSearchQuery] = useState("")
  const deferredSearchQuery = useDeferredValue(searchQuery)
  const [statusFilter, setStatusFilter] = useState<SpmFindingStatus[]>([])
  const [statusMode, setStatusMode] = useState<FilterMode>("include")
  const [severityFilter, setSeverityFilter] = useState<SpmSeverity[]>([])
  const [severityMode, setSeverityMode] = useState<FilterMode>("include")
  const [endpointFilter, setEndpointFilter] = useState<string[]>([])
  const [endpointMode, setEndpointMode] = useState<FilterMode>("include")
  const [controlFilter, setControlFilter] = useState<string[]>([])
  const [controlMode, setControlMode] = useState<FilterMode>("include")
  const findingsQuery = useSpmFindings()
  const assets = assetsQuery.data?.items ?? []
  const controls = controlsQuery.data ?? []
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

  function resetFilters() {
    setSearchQuery("")
    setStatusFilter([])
    setStatusMode("include")
    setSeverityFilter([])
    setSeverityMode("include")
    setEndpointFilter([])
    setEndpointMode("include")
    setControlFilter([])
    setControlMode("include")
  }

  const filteredFindings = findings.filter((finding) => {
    const asset = getAssetRecord(finding.asset_id, assets)
    const endpointName = getEndpointName(finding.endpoint_id, endpoints)
    const statusMatches =
      statusFilter.length === 0 ||
      statusFilter.includes(finding.status) === (statusMode === "include")
    const severityMatches =
      severityFilter.length === 0 ||
      severityFilter.includes(finding.severity) === (severityMode === "include")
    const endpointMatches =
      endpointFilter.length === 0 ||
      endpointFilter.includes(finding.endpoint_id) ===
        (endpointMode === "include")
    const controlMatches =
      controlFilter.length === 0 ||
      controlFilter.includes(finding.control_id) === (controlMode === "include")

    return (
      includesQuery(
        [
          finding.summary,
          finding.control_id,
          finding.control_key,
          finding.status,
          finding.severity,
          finding.asset_type,
          finding.asset_class,
          asset?.display_name,
          asset ? getAssetPath(asset) : finding.asset_id,
          endpointName,
        ],
        deferredSearchQuery
      ) &&
      statusMatches &&
      severityMatches &&
      endpointMatches &&
      controlMatches
    )
  })

  const groupedFindings = FINDING_STATUS_ORDER.map((status) => ({
    status,
    items: filteredFindings.filter((finding) => finding.status === status),
  }))
  const hasFilters =
    searchQuery.trim().length > 0 ||
    statusFilter.length > 0 ||
    severityFilter.length > 0 ||
    endpointFilter.length > 0 ||
    controlFilter.length > 0

  return renderMaybeLoading(
    entitlementLoading ||
      assetsQuery.isLoading ||
      controlsQuery.isLoading ||
      endpointsQuery.isLoading ||
      findingsQuery.isLoading,
    hasEntitlement("spm"),
    "SPM entitlement required",
    "This organization does not have access to AI SPM yet.",
    <SpmListShell
      title="Findings"
      icon={ShieldAlertIcon}
      searchQuery={searchQuery}
      onSearchChange={setSearchQuery}
      searchPlaceholder="Search findings..."
      count={filteredFindings.length}
      countLabel="findings"
      hasFilters={hasFilters}
      resetButton={<ResetFiltersButton onClick={resetFilters} />}
      filters={
        <>
          <MultiFilterSelect
            label="Status"
            icon={CircleDotIcon}
            value={statusFilter}
            options={withoutAll<SpmFindingStatus>(FINDING_STATUS_OPTIONS)}
            mode={statusMode}
            onModeChange={setStatusMode}
            onChange={setStatusFilter}
          />
          <MultiFilterSelect
            label="Severity"
            icon={ShieldAlertIcon}
            value={severityFilter}
            options={withoutAll<SpmSeverity>(SEVERITY_OPTIONS)}
            mode={severityMode}
            onModeChange={setSeverityMode}
            onChange={setSeverityFilter}
          />
          <MultiFilterSelect
            label="Endpoint"
            icon={ComputerIcon}
            value={endpointFilter}
            options={endpointOptions(endpoints).filter(
              (option) => option.value !== ALL_VALUE
            )}
            mode={endpointMode}
            onModeChange={setEndpointMode}
            onChange={setEndpointFilter}
          />
          <MultiFilterSelect
            label="Control"
            icon={FileSearchIcon}
            value={controlFilter}
            options={controlOptions(controls).filter(
              (option) => option.value !== ALL_VALUE
            )}
            mode={controlMode}
            onModeChange={setControlMode}
            onChange={setControlFilter}
          />
        </>
      }
    >
      {filteredFindings.length === 0 ? (
        <SpmEmptyState
          title={findings.length === 0 ? "No findings" : EMPTY_FILTERS}
          description={
            findings.length === 0
              ? "Once endpoints sync inventory, control failures will appear here."
              : "Adjust search or filters to find another finding."
          }
          icon={<ShieldAlertIcon className="h-6 w-6" />}
        />
      ) : (
        <SpmAccordion
          groups={groupedFindings.map((group) => {
            const config = findingStatusStyles[group.status]
            return {
              value: group.status,
              label: config.label,
              count: group.items.length,
              icon: config.icon,
              iconClassName: config.iconClassName,
              triggerClassName: config.triggerClassName,
            }
          })}
        >
          {(status) =>
            groupedFindings
              .find((group) => group.status === status)
              ?.items.map((finding) => (
                <FindingRow
                  key={finding.id}
                  assets={assets}
                  busyDecision={busyDecision}
                  endpoints={endpoints}
                  finding={finding}
                  onDecision={handleDecision}
                />
              ))
          }
        </SpmAccordion>
      )}
    </SpmListShell>
  )
}

/**
 * Assets feed for the current SPM catalog.
 */
export function SpmAssetsView() {
  const { hasEntitlement, isLoading: entitlementLoading } = useEntitlements()
  const endpointsQuery = useSpmEndpoints()
  const [searchQuery, setSearchQuery] = useState("")
  const deferredSearchQuery = useDeferredValue(searchQuery)
  const [selectedAssetClass, setSelectedAssetClass] = useState<
    SpmAssetClass | typeof ALL_VALUE
  >(ALL_VALUE)
  const [selectedAssetType, setSelectedAssetType] = useState<
    SpmAssetType | typeof ALL_VALUE
  >(ALL_VALUE)
  const [selectedEndpointId, setSelectedEndpointId] = useState(ALL_VALUE)
  const [selectedHarness, setSelectedHarness] = useState<
    SpmHarness | typeof ALL_VALUE
  >(ALL_VALUE)
  const assetsQuery = useSpmAssets({
    assetClass:
      selectedAssetClass === ALL_VALUE ? undefined : selectedAssetClass,
    assetType: selectedAssetType === ALL_VALUE ? undefined : selectedAssetType,
    endpointId:
      selectedEndpointId === ALL_VALUE ? undefined : selectedEndpointId,
    harness: selectedHarness === ALL_VALUE ? undefined : selectedHarness,
  })
  const assets = assetsQuery.data?.items ?? []
  const endpoints = endpointsQuery.data?.items ?? []

  function resetFilters() {
    setSearchQuery("")
    setSelectedAssetClass(ALL_VALUE)
    setSelectedAssetType(ALL_VALUE)
    setSelectedEndpointId(ALL_VALUE)
    setSelectedHarness(ALL_VALUE)
  }

  const filteredAssets = assets.filter((asset) =>
    includesQuery(
      [
        asset.display_name,
        getAssetPath(asset),
        asset.harness,
        asset.asset_class,
        asset.asset_type,
      ],
      deferredSearchQuery
    )
  )
  const groupedAssets = groupByHarness(filteredAssets)
  const hasFilters =
    searchQuery.trim().length > 0 ||
    selectedAssetClass !== ALL_VALUE ||
    selectedAssetType !== ALL_VALUE ||
    selectedEndpointId !== ALL_VALUE ||
    selectedHarness !== ALL_VALUE

  return renderMaybeLoading(
    entitlementLoading || endpointsQuery.isLoading || assetsQuery.isLoading,
    hasEntitlement("spm"),
    "SPM entitlement required",
    "This organization does not have access to AI SPM yet.",
    <SpmListShell
      title="Assets"
      icon={PackageIcon}
      searchQuery={searchQuery}
      onSearchChange={setSearchQuery}
      searchPlaceholder="Search assets..."
      count={filteredAssets.length}
      countLabel="assets"
      hasFilters={hasFilters}
      resetButton={<ResetFiltersButton onClick={resetFilters} />}
      filters={
        <>
          <FilterSelect
            label="Harness"
            icon={RadarIcon}
            value={selectedHarness}
            options={HARNESS_OPTIONS}
            onChange={setSelectedHarness}
          />
          <FilterSelect
            label="Endpoint"
            icon={ComputerIcon}
            value={selectedEndpointId}
            options={endpointOptions(endpoints)}
            onChange={setSelectedEndpointId}
          />
          <FilterSelect
            label="Asset class"
            icon={LayersIcon}
            value={selectedAssetClass}
            options={ASSET_CLASS_OPTIONS}
            onChange={setSelectedAssetClass}
          />
          <FilterSelect
            label="Asset type"
            icon={PackageIcon}
            value={selectedAssetType}
            options={ASSET_TYPE_OPTIONS}
            onChange={setSelectedAssetType}
          />
        </>
      }
    >
      {filteredAssets.length === 0 ? (
        <SpmEmptyState
          title={assets.length === 0 ? "No assets" : EMPTY_FILTERS}
          description={
            assets.length === 0
              ? "Endpoint inventory will appear here after a successful sync."
              : "Adjust search or filters to find another asset."
          }
          icon={<PackageIcon className="h-6 w-6" />}
        />
      ) : (
        <SpmAccordion
          groups={groupedAssets.map((group) => ({
            value: group.harness,
            label: harnessLabel(group.harness),
            count: group.items.length,
            icon: RadarIcon,
            iconClassName:
              group.harness === "claude_code"
                ? "text-amber-600"
                : "text-muted-foreground",
            triggerClassName: harnessTriggerClassName(group.harness),
          }))}
        >
          {(harness) =>
            groupedAssets
              .find((group) => group.harness === harness)
              ?.items.map((asset) => <AssetRow key={asset.id} asset={asset} />)
          }
        </SpmAccordion>
      )}
    </SpmListShell>
  )
}

/**
 * Controls feed grouped by severity.
 */
export function SpmControlsView() {
  const { hasEntitlement, isLoading: entitlementLoading } = useEntitlements()
  const controlsQuery = useSpmControls()
  const [searchQuery, setSearchQuery] = useState("")
  const deferredSearchQuery = useDeferredValue(searchQuery)
  const [severityFilter, setSeverityFilter] = useState<SpmSeverity[]>([])
  const [severityMode, setSeverityMode] = useState<FilterMode>("include")
  const [assetClassFilter, setAssetClassFilter] = useState<SpmAssetClass[]>([])
  const [assetClassMode, setAssetClassMode] = useState<FilterMode>("include")
  const controls = controlsQuery.data ?? []

  function resetFilters() {
    setSearchQuery("")
    setSeverityFilter([])
    setSeverityMode("include")
    setAssetClassFilter([])
    setAssetClassMode("include")
  }

  const visibleControls = controls.filter((control) => {
    const severityMatches =
      severityFilter.length === 0 ||
      severityFilter.includes(control.severity) === (severityMode === "include")
    const assetClassMatches =
      assetClassFilter.length === 0 ||
      assetClassFilter.includes(control.asset_class) ===
        (assetClassMode === "include")

    return (
      includesQuery(
        [
          control.id,
          control.key,
          control.title,
          control.description,
          control.severity,
          control.asset_class,
          control.asset_type,
          control.action,
        ],
        deferredSearchQuery
      ) &&
      severityMatches &&
      assetClassMatches
    )
  })
  const groupedControls = SEVERITY_ORDER.map((severity) => ({
    severity,
    items: visibleControls.filter((control) => control.severity === severity),
  }))
  const hasFilters =
    searchQuery.trim().length > 0 ||
    severityFilter.length > 0 ||
    assetClassFilter.length > 0

  return renderMaybeLoading(
    entitlementLoading || controlsQuery.isLoading,
    hasEntitlement("spm"),
    "SPM entitlement required",
    "This organization does not have access to AI SPM yet.",
    <SpmListShell
      title="Controls"
      icon={FileSearchIcon}
      searchQuery={searchQuery}
      onSearchChange={setSearchQuery}
      searchPlaceholder="Search controls..."
      count={visibleControls.length}
      countLabel="controls"
      hasFilters={hasFilters}
      resetButton={<ResetFiltersButton onClick={resetFilters} />}
      filters={
        <>
          <MultiFilterSelect
            label="Severity"
            icon={ShieldAlertIcon}
            value={severityFilter}
            options={withoutAll<SpmSeverity>(SEVERITY_OPTIONS)}
            mode={severityMode}
            onModeChange={setSeverityMode}
            onChange={setSeverityFilter}
          />
          <MultiFilterSelect
            label="Asset class"
            icon={LayersIcon}
            value={assetClassFilter}
            options={withoutAll<SpmAssetClass>(ASSET_CLASS_OPTIONS)}
            mode={assetClassMode}
            onModeChange={setAssetClassMode}
            onChange={setAssetClassFilter}
          />
        </>
      }
    >
      {controls.length === 0 ? (
        <SpmEmptyState
          title="No controls"
          description="The generated client did not return any SPM controls."
          icon={<FileSearchIcon className="h-6 w-6" />}
        />
      ) : visibleControls.length === 0 ? (
        <SpmEmptyState
          title={EMPTY_FILTERS}
          description="Adjust search or filters to find another control."
          icon={<FileSearchIcon className="h-6 w-6" />}
        />
      ) : (
        <SpmAccordion
          groups={groupedControls.map((group) => {
            const config = severityStyles[group.severity]
            const triggerClassName =
              group.severity === "critical"
                ? "data-[state=open]:border-l-fuchsia-600 data-[state=open]:bg-fuchsia-600/[0.03] dark:data-[state=open]:bg-fuchsia-600/[0.08]"
                : group.severity === "high"
                  ? "data-[state=open]:border-l-red-600 data-[state=open]:bg-red-600/[0.03] dark:data-[state=open]:bg-red-600/[0.08]"
                  : group.severity === "medium"
                    ? "data-[state=open]:border-l-orange-600 data-[state=open]:bg-orange-600/[0.03] dark:data-[state=open]:bg-orange-600/[0.08]"
                    : "data-[state=open]:border-l-yellow-600 data-[state=open]:bg-yellow-600/[0.03] dark:data-[state=open]:bg-yellow-600/[0.08]"
            return {
              value: group.severity,
              label: config.label,
              count: group.items.length,
              icon: config.icon,
              iconClassName:
                group.severity === "critical"
                  ? "text-fuchsia-600"
                  : group.severity === "high"
                    ? "text-red-600"
                    : group.severity === "medium"
                      ? "text-orange-600"
                      : "text-yellow-600",
              triggerClassName,
            }
          })}
        >
          {(severity) =>
            groupedControls
              .find((group) => group.severity === severity)
              ?.items.map((control) => (
                <ControlRow key={control.id} control={control} />
              ))
          }
        </SpmAccordion>
      )}
    </SpmListShell>
  )
}
