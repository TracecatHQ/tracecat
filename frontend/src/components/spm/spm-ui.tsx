"use client"

import {
  BoxIcon,
  CircleDotIcon,
  Clock3Icon,
  ComputerIcon,
  FileSearchIcon,
  LayersIcon,
  PackageIcon,
  RadarIcon,
  ShieldAlertIcon,
  ShieldCheckIcon,
  TerminalSquareIcon,
  WrenchIcon,
} from "lucide-react"
import Link from "next/link"
import { useDeferredValue, useState } from "react"
import type {
  SpmAssetClass,
  SpmAssetRead,
  SpmAssetType,
  SpmEndpointRead,
  SpmEndpointStatus,
  SpmFindingStatus,
  SpmHarness,
  SpmSeverity,
} from "@/client"
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
import { Button } from "@/components/ui/button"
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
import {
  ALL_VALUE,
  canCancelPendingEnrollment,
  EMPTY_FILTERS,
  endpointStatusVariant,
  type FindingDecision,
  formatLabel,
  formatRelativeTimestamp,
  getAssetPath,
  getAssetRecord,
  getComplianceRollup,
  getEndpointName,
  getObservedState,
  getPolicyScope,
  includesQuery,
  renderMaybeLoading,
  severityVariant,
} from "./spm-common"
import {
  ASSET_CLASS_OPTIONS,
  ASSET_TYPE_OPTIONS,
  COMPLIANCE_OPTIONS,
  controlOptions,
  ENDPOINT_STATUS_OPTIONS,
  endpointOptions,
  FINDING_STATUS_OPTIONS,
  FilterSelect,
  HARNESS_OPTIONS,
  ResetFiltersButton,
  SEVERITY_OPTIONS,
  SYNC_OPTIONS,
} from "./spm-filters"
import { FindingRow } from "./spm-findings"
import { SpmInstallDrawer } from "./spm-install-drawer"
import {
  FeedRow,
  FeedSection,
  SmallBadge,
  SpmDetailShell,
  SpmEmptyState,
  SpmListShell,
} from "./spm-layout"

export { SpmInstallDrawer } from "./spm-install-drawer"

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
  const [complianceFilter, setComplianceFilter] =
    useState<(typeof COMPLIANCE_OPTIONS)[number]["value"]>(ALL_VALUE)
  const [syncFilter, setSyncFilter] =
    useState<(typeof SYNC_OPTIONS)[number]["value"]>(ALL_VALUE)
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

  function resetFilters() {
    setSearchQuery("")
    setStatusFilter(ALL_VALUE)
    setComplianceFilter(ALL_VALUE)
    setSyncFilter(ALL_VALUE)
  }

  const filteredEndpoints = endpoints.filter((endpoint) => {
    const rollup = getComplianceRollup(endpoint.id, findings)
    const syncKey = endpoint.last_sync_error ? "error" : "healthy"
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
          rollup.label,
        ],
        deferredSearchQuery
      ) &&
      (statusFilter === ALL_VALUE || endpoint.status === statusFilter) &&
      (complianceFilter === ALL_VALUE || rollup.key === complianceFilter) &&
      (syncFilter === ALL_VALUE || syncKey === syncFilter)
    )
  })

  const hasFilters =
    searchQuery.trim().length > 0 ||
    statusFilter !== ALL_VALUE ||
    complianceFilter !== ALL_VALUE ||
    syncFilter !== ALL_VALUE

  return renderMaybeLoading(
    entitlementLoading || endpointsQuery.isLoading || findingsQuery.isLoading,
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
            options={[...COMPLIANCE_OPTIONS]}
            onChange={setComplianceFilter}
          />
          <FilterSelect
            label="Sync"
            icon={Clock3Icon}
            value={syncFilter}
            options={[...SYNC_OPTIONS]}
            onChange={setSyncFilter}
          />
        </>
      }
    >
      {filteredEndpoints.length === 0 ? (
        <SpmEmptyState
          title={endpoints.length === 0 ? "No endpoints yet" : EMPTY_FILTERS}
          description={
            endpoints.length === 0
              ? "Create an endpoint enrollment to generate local install commands."
              : "Adjust search or filters to find another endpoint."
          }
          icon={<ComputerIcon className="h-6 w-6" />}
        />
      ) : (
        <FeedSection>
          {filteredEndpoints.map((endpoint) => {
            const rollup = getComplianceRollup(endpoint.id, findings)
            const hasSyncError = Boolean(endpoint.last_sync_error)
            return (
              <FeedRow
                key={endpoint.id}
                icon={<ComputerIcon className="size-4 text-muted-foreground" />}
                title={
                  <Link
                    href={`/watchtower/endpoints/${endpoint.id}`}
                    className="underline-offset-4 hover:underline"
                  >
                    {endpoint.name}
                  </Link>
                }
                subtitle={`${formatLabel(endpoint.harness)} on ${formatLabel(endpoint.platform)} · ${endpoint.hostname ?? "No hostname"}`}
                badges={
                  <>
                    <SmallBadge
                      variant={endpointStatusVariant(endpoint.status)}
                    >
                      {formatLabel(endpoint.status)}
                    </SmallBadge>
                    <SmallBadge variant={rollup.variant}>
                      {rollup.label}
                    </SmallBadge>
                    <SmallBadge
                      variant={hasSyncError ? "destructive" : "outline"}
                    >
                      {hasSyncError ? "Last sync failed" : "No sync error"}
                    </SmallBadge>
                  </>
                }
                meta={
                  <>
                    <span>{rollup.detail}</span>
                    <span>
                      Seen {formatRelativeTimestamp(endpoint.last_seen_at)}
                    </span>
                    <span>
                      Synced {formatRelativeTimestamp(endpoint.last_sync_at)}
                    </span>
                  </>
                }
                actions={
                  canCancelPendingEnrollment(endpoint) ? (
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-7 px-2 text-xs"
                      onClick={() => setDeleteCandidate(endpoint)}
                    >
                      Cancel enrollment
                    </Button>
                  ) : null
                }
              />
            )
          })}
        </FeedSection>
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
  const [statusFilter, setStatusFilter] = useState<
    SpmFindingStatus | typeof ALL_VALUE
  >(ALL_VALUE)
  const [severityFilter, setSeverityFilter] = useState<
    SpmSeverity | typeof ALL_VALUE
  >(ALL_VALUE)
  const [endpointFilter, setEndpointFilter] = useState(ALL_VALUE)
  const [controlFilter, setControlFilter] = useState(ALL_VALUE)
  const findingsQuery = useSpmFindings({
    controlId: controlFilter === ALL_VALUE ? undefined : controlFilter,
    endpointId: endpointFilter === ALL_VALUE ? undefined : endpointFilter,
  })
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
    setStatusFilter(ALL_VALUE)
    setSeverityFilter(ALL_VALUE)
    setEndpointFilter(ALL_VALUE)
    setControlFilter(ALL_VALUE)
  }

  const filteredFindings = findings.filter((finding) => {
    const asset = getAssetRecord(finding.asset_id, assets)
    const endpointName = getEndpointName(finding.endpoint_id, endpoints)
    return (
      includesQuery(
        [
          finding.summary,
          finding.control_id,
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
      (statusFilter === ALL_VALUE || finding.status === statusFilter) &&
      (severityFilter === ALL_VALUE || finding.severity === severityFilter)
    )
  })

  const hasFilters =
    searchQuery.trim().length > 0 ||
    statusFilter !== ALL_VALUE ||
    severityFilter !== ALL_VALUE ||
    endpointFilter !== ALL_VALUE ||
    controlFilter !== ALL_VALUE

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
          <FilterSelect
            label="Status"
            icon={CircleDotIcon}
            value={statusFilter}
            options={FINDING_STATUS_OPTIONS}
            onChange={setStatusFilter}
          />
          <FilterSelect
            label="Severity"
            icon={ShieldAlertIcon}
            value={severityFilter}
            options={SEVERITY_OPTIONS}
            onChange={setSeverityFilter}
          />
          <FilterSelect
            label="Endpoint"
            icon={ComputerIcon}
            value={endpointFilter}
            options={endpointOptions(endpoints)}
            onChange={setEndpointFilter}
          />
          <FilterSelect
            label="Control"
            icon={FileSearchIcon}
            value={controlFilter}
            options={controlOptions(controls)}
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
        <FeedSection>
          {filteredFindings.map((finding) => (
            <FindingRow
              key={finding.id}
              assets={assets}
              busyDecision={busyDecision}
              controls={controls}
              endpoints={endpoints}
              finding={finding}
              onDecision={handleDecision}
            />
          ))}
        </FeedSection>
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
        <FeedSection>
          {filteredAssets.map((asset) => {
            const scope = getPolicyScope(asset.asset_type)
            return (
              <FeedRow
                key={asset.id}
                icon={<PackageIcon className="size-4 text-muted-foreground" />}
                title={asset.display_name}
                subtitle={getAssetPath(asset)}
                badges={
                  <>
                    <SmallBadge>{formatLabel(asset.harness)}</SmallBadge>
                    <SmallBadge>{formatLabel(asset.asset_class)}</SmallBadge>
                    <SmallBadge>{formatLabel(asset.asset_type)}</SmallBadge>
                    <SmallBadge variant={scope.variant}>
                      {scope.label}
                    </SmallBadge>
                  </>
                }
                meta={
                  <>
                    <span>{scope.description}</span>
                    <span>
                      Seen {formatRelativeTimestamp(asset.last_seen_at)}
                    </span>
                  </>
                }
              />
            )
          })}
        </FeedSection>
      )}
    </SpmListShell>
  )
}

/**
 * Controls feed with a right-side detail panel.
 */
export function SpmControlsView() {
  const { hasEntitlement, isLoading: entitlementLoading } = useEntitlements()
  const controlsQuery = useSpmControls()
  const endpointsQuery = useSpmEndpoints()
  const [selectedControlId, setSelectedControlId] = useState<string | null>(
    null
  )
  const [searchQuery, setSearchQuery] = useState("")
  const deferredSearchQuery = useDeferredValue(searchQuery)
  const [severityFilter, setSeverityFilter] = useState<
    SpmSeverity | typeof ALL_VALUE
  >(ALL_VALUE)
  const [assetClassFilter, setAssetClassFilter] = useState<
    SpmAssetClass | typeof ALL_VALUE
  >(ALL_VALUE)
  const controls = controlsQuery.data ?? []
  const activeControlId = selectedControlId ?? controls[0]?.id
  const findingsQuery = useSpmFindings({
    controlId: activeControlId,
  })
  const endpoints = endpointsQuery.data?.items ?? []
  const findings = findingsQuery.data?.items ?? []
  const selectedControl =
    controls.find((control) => control.id === activeControlId) ?? controls[0]
  const impactedEndpointIds = Array.from(
    new Set(findings.map((finding) => finding.endpoint_id))
  )

  function resetFilters() {
    setSearchQuery("")
    setSeverityFilter(ALL_VALUE)
    setAssetClassFilter(ALL_VALUE)
  }

  const visibleControls = controls.filter(
    (control) =>
      includesQuery(
        [
          control.id,
          control.title,
          control.description,
          control.severity,
          control.asset_class,
          control.asset_type,
          control.action,
        ],
        deferredSearchQuery
      ) &&
      (severityFilter === ALL_VALUE || control.severity === severityFilter) &&
      (assetClassFilter === ALL_VALUE ||
        control.asset_class === assetClassFilter)
  )

  const hasFilters =
    searchQuery.trim().length > 0 ||
    severityFilter !== ALL_VALUE ||
    assetClassFilter !== ALL_VALUE

  return renderMaybeLoading(
    entitlementLoading ||
      controlsQuery.isLoading ||
      endpointsQuery.isLoading ||
      findingsQuery.isLoading,
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
          <FilterSelect
            label="Severity"
            icon={ShieldAlertIcon}
            value={severityFilter}
            options={SEVERITY_OPTIONS}
            onChange={setSeverityFilter}
          />
          <FilterSelect
            label="Asset class"
            icon={LayersIcon}
            value={assetClassFilter}
            options={ASSET_CLASS_OPTIONS}
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
      ) : (
        <div className="grid h-full min-h-0 grid-cols-1 lg:grid-cols-[minmax(0,1fr)_minmax(360px,0.8fr)]">
          <div className="min-h-0 overflow-auto">
            {visibleControls.length === 0 ? (
              <SpmEmptyState
                title={EMPTY_FILTERS}
                description="Adjust search or filters to find another control."
                icon={<FileSearchIcon className="h-6 w-6" />}
              />
            ) : (
              <FeedSection>
                {visibleControls.map((control) => {
                  const isSelected = control.id === selectedControl?.id
                  return (
                    <FeedRow
                      key={control.id}
                      isSelected={isSelected}
                      onClick={() => setSelectedControlId(control.id)}
                      icon={
                        <FileSearchIcon className="size-4 text-muted-foreground" />
                      }
                      title={control.title}
                      subtitle={control.id}
                      badges={
                        <>
                          <SmallBadge
                            variant={severityVariant(control.severity)}
                          >
                            {formatLabel(control.severity)}
                          </SmallBadge>
                          <SmallBadge>
                            {formatLabel(control.asset_class)}
                          </SmallBadge>
                          <SmallBadge>
                            {formatLabel(control.asset_type)}
                          </SmallBadge>
                          <SmallBadge icon={WrenchIcon}>
                            {formatLabel(control.action)}
                          </SmallBadge>
                        </>
                      }
                    />
                  )
                })}
              </FeedSection>
            )}
          </div>
          <div className="min-h-0 border-t lg:border-l lg:border-t-0">
            {selectedControl ? (
              <div className="flex h-full min-h-0 flex-col">
                <div className="shrink-0 border-b px-4 py-3">
                  <div className="flex items-start gap-3">
                    <FileSearchIcon className="mt-0.5 size-4 text-muted-foreground" />
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-medium">
                        {selectedControl.title}
                      </div>
                      <div className="mt-1 text-xs text-muted-foreground">
                        {selectedControl.description}
                      </div>
                      <div className="mt-2 flex flex-wrap items-center gap-1">
                        <SmallBadge
                          variant={severityVariant(selectedControl.severity)}
                        >
                          {formatLabel(selectedControl.severity)}
                        </SmallBadge>
                        <SmallBadge>
                          {formatLabel(selectedControl.asset_class)} /{" "}
                          {formatLabel(selectedControl.asset_type)}
                        </SmallBadge>
                        <SmallBadge icon={RadarIcon}>
                          {formatLabel(selectedControl.harness)}
                        </SmallBadge>
                      </div>
                    </div>
                  </div>
                </div>
                <div className="min-h-0 flex-1 overflow-auto">
                  <FeedSection title="Impacted endpoints">
                    {impactedEndpointIds.length > 0 ? (
                      impactedEndpointIds.map((endpointId) => (
                        <FeedRow
                          key={endpointId}
                          icon={
                            <ComputerIcon className="size-4 text-muted-foreground" />
                          }
                          title={getEndpointName(endpointId, endpoints)}
                          subtitle={endpointId}
                        />
                      ))
                    ) : (
                      <div className="px-3 py-6 text-sm text-muted-foreground">
                        No current findings for this control.
                      </div>
                    )}
                  </FeedSection>
                  <FeedSection title="Matching findings">
                    {findings.length > 0 ? (
                      findings.map((finding) => (
                        <FindingRow
                          key={finding.id}
                          assets={[]}
                          busyDecision={null}
                          controls={controls}
                          endpoints={endpoints}
                          finding={finding}
                        />
                      ))
                    ) : (
                      <div className="px-3 py-6 text-sm text-muted-foreground">
                        No findings currently match this control.
                      </div>
                    )}
                  </FeedSection>
                </div>
              </div>
            ) : (
              <SpmEmptyState
                title="Select a control"
                description="Choose a control to inspect related endpoints and findings."
                icon={<FileSearchIcon className="h-6 w-6" />}
              />
            )}
          </div>
        </div>
      )}
    </SpmListShell>
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
    <SpmDetailShell
      backHref="/watchtower/endpoints"
      backLabel="Back to endpoints"
      icon={TerminalSquareIcon}
      title={endpoint?.name ?? "Endpoint"}
      subtitle="Endpoint metadata, latest sync state, inventory, and findings."
    >
      {endpoint ? (
        <>
          <FeedSection title="Endpoint overview">
            <FeedRow
              icon={<ComputerIcon className="size-4 text-muted-foreground" />}
              title="Endpoint status"
              subtitle={`${endpoint.hostname ?? "No hostname"} · ${endpoint.os_user ?? "Unknown user"}`}
              badges={
                <>
                  <SmallBadge variant={endpointStatusVariant(endpoint.status)}>
                    {formatLabel(endpoint.status)}
                  </SmallBadge>
                  <SmallBadge>{formatLabel(endpoint.harness)}</SmallBadge>
                  <SmallBadge>{formatLabel(endpoint.platform)}</SmallBadge>
                </>
              }
              meta={
                <>
                  <span>Version {endpoint.endpoint_version ?? "Unknown"}</span>
                  <span>
                    Seen {formatRelativeTimestamp(endpoint.last_seen_at)}
                  </span>
                  <span>
                    Synced {formatRelativeTimestamp(endpoint.last_sync_at)}
                  </span>
                </>
              }
            />
            <FeedRow
              icon={<Clock3Icon className="size-4 text-muted-foreground" />}
              title="Latest sync state"
              subtitle={endpoint.last_sync_error ?? "No sync error reported."}
              badges={
                <SmallBadge
                  variant={endpoint.last_sync_error ? "destructive" : "outline"}
                >
                  {endpoint.last_sync_error
                    ? "Last sync failed"
                    : "No sync error"}
                </SmallBadge>
              }
            />
            <FeedRow
              icon={<BoxIcon className="size-4 text-muted-foreground" />}
              title="Local paths"
              subtitle={endpoint.home_path ?? "Unknown home path"}
              badges={
                <SmallBadge>
                  {endpoint.client_metadata
                    ? "Client metadata available"
                    : "No client metadata"}
                </SmallBadge>
              }
            />
          </FeedSection>
          <FeedSection title="Endpoint assets">
            {endpointAssets.length > 0 ? (
              endpointAssets.map((asset) => {
                const scope = getPolicyScope(asset.asset_type)
                const observedState = getObservedState(asset)
                return (
                  <FeedRow
                    key={asset.asset_sighting_id}
                    icon={
                      <PackageIcon className="size-4 text-muted-foreground" />
                    }
                    title={asset.display_name}
                    subtitle={getAssetPath(asset)}
                    badges={
                      <>
                        <SmallBadge>
                          {formatLabel(asset.asset_class)}
                        </SmallBadge>
                        <SmallBadge>{formatLabel(asset.asset_type)}</SmallBadge>
                        <SmallBadge variant={scope.variant}>
                          {scope.label}
                        </SmallBadge>
                        <SmallBadge variant={observedState.variant}>
                          {observedState.label}
                        </SmallBadge>
                      </>
                    }
                    meta={
                      <>
                        <span>{observedState.detail}</span>
                        <span>
                          Seen {formatRelativeTimestamp(asset.last_seen_at)}
                        </span>
                      </>
                    }
                  />
                )
              })
            ) : (
              <div className="px-3 py-6 text-sm text-muted-foreground">
                This endpoint has not reported any inventory yet.
              </div>
            )}
          </FeedSection>
          <FeedSection title="Endpoint findings">
            {findings.length > 0 ? (
              findings.map((finding) => (
                <FindingRow
                  key={finding.id}
                  assets={dedupedAssets}
                  busyDecision={busyDecision}
                  endpoints={endpointsQuery.data?.items ?? []}
                  finding={finding}
                  onDecision={handleDecision}
                  showEndpoint={false}
                />
              ))
            ) : (
              <div className="px-3 py-6 text-sm text-muted-foreground">
                No control failures are currently open for this endpoint.
              </div>
            )}
          </FeedSection>
        </>
      ) : (
        <SpmEmptyState
          title="Endpoint not found"
          description="The requested endpoint did not load."
          icon={<ComputerIcon className="h-6 w-6" />}
        />
      )}
    </SpmDetailShell>
  )
}
