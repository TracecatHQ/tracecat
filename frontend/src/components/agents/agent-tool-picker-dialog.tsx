"use client"

import { useVirtualizer } from "@tanstack/react-virtual"
import { Minus, Search } from "lucide-react"
import {
  type KeyboardEvent,
  memo,
  useCallback,
  useDeferredValue,
  useId,
  useMemo,
  useRef,
  useState,
} from "react"
import { useFormContext } from "react-hook-form"
import { getIcon, getMcpProviderIconId, ProviderIcon } from "@/components/icons"
import { Button } from "@/components/ui/button"
import { CheckIndicator } from "@/components/ui/check-indicator"
import { Checkbox } from "@/components/ui/checkbox"
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog"
import { Kbd } from "@/components/ui/kbd"
import {
  applyToolSelection,
  type PresetToolFields,
  removeTools,
  searchTools,
  type ToolEntry,
  type ToolGroup,
  type ToolIndex,
} from "@/lib/agent-preset-tools"
import { cn } from "@/lib/utils"

interface PickerProps {
  index: ToolIndex
  open: boolean
  onOpenChange: (open: boolean) => void
  maxTools?: number | null
  disabled?: boolean
}

type PickerRow =
  | {
      kind: "header"
      id: string
      title: string
      group?: ToolGroup
    }
  | { kind: "tool"; id: string; entry: ToolEntry }

type RailRow =
  | { kind: "label"; id: string; title: string }
  | {
      kind: "source"
      id: string
      title: string
      entry?: ToolEntry
      total: number
    }

/** Lazily mount a virtualized catalog with local, cancelable selection. */
export function AgentToolPickerDialog({ open, ...props }: PickerProps) {
  const searchRef = useRef<HTMLInputElement>(null)
  return (
    <Dialog open={open} onOpenChange={props.onOpenChange}>
      {open && (
        <DialogContent
          aria-describedby={undefined}
          onOpenAutoFocus={(event) => {
            event.preventDefault()
            searchRef.current?.focus()
          }}
          className="flex h-[min(92dvh,960px)] w-[min(96vw,1440px)] max-w-none flex-col gap-0 overflow-hidden p-0 shadow-none [&>button]:hidden"
        >
          <DialogTitle className="sr-only">Add tools</DialogTitle>

          <PickerBody {...props} searchRef={searchRef} />
        </DialogContent>
      )}
    </Dialog>
  )
}

function PickerBody({
  index,
  onOpenChange,
  maxTools,
  disabled,
  searchRef,
}: Omit<PickerProps, "open"> & {
  searchRef: React.RefObject<HTMLInputElement>
}) {
  const { getValues, setValue } = useFormContext<PresetToolFields>()
  const [selection, setSelection] = useState(() => ({
    actions: new Set(getValues("actions")),
    mcp: new Set(getValues("mcpIntegrations")),
  }))
  const [query, setQuery] = useState("")
  const deferredQuery = useDeferredValue(query)
  const [source, setSource] = useState("all")
  const [activeId, setActiveId] = useState<string | null>(null)
  const railRef = useRef<HTMLElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const listId = useId()
  const count = selection.actions.size
  const toolLabel = count === 1 ? "tool" : "tools"
  const selectedIds = useMemo(
    () =>
      new Set([
        ...selection.actions,
        ...[...selection.mcp].map((id) => `mcp:${id}`),
      ]),
    [selection]
  )
  const selectedGroup = index.groups.find((group) => group.id === source)
  const matches = useMemo(
    () => searchTools(index, deferredQuery.trim()),
    [index, deferredQuery]
  )
  const selectedFilter = source === "selected" ? selectedIds : null
  const rows = useMemo<PickerRow[]>(() => {
    if (deferredQuery.trim())
      return matches.map((entry) => ({ kind: "tool", id: entry.id, entry }))
    const result: PickerRow[] = []
    for (const group of index.groups) {
      if (source !== "all" && source !== "selected" && source !== group.id)
        continue
      const entries =
        source === "selected"
          ? group.entries.filter((entry) => selectedFilter?.has(entry.id))
          : group.entries
      if (!entries.length) continue
      if (source === "all")
        result.push({
          kind: "header",
          id: `header:${group.id}`,
          title: group.title,
          group,
        })
      for (const entry of entries)
        result.push({ kind: "tool", id: entry.id, entry })
    }
    return result
  }, [index, source, selectedFilter, deferredQuery, matches])
  const optionIndices = useMemo(
    () => rows.flatMap((row, i) => (row.kind === "tool" ? [i] : [])),
    [rows]
  )
  const activeIndex = rows.findIndex(
    (row) => row.kind === "tool" && row.id === activeId
  )
  const effectiveActiveIndex =
    activeIndex < 0 ? (optionIndices[0] ?? -1) : activeIndex
  // Keep callback identities stable across selection and active-row changes.
  const getItemKey = useCallback((i: number) => rows[i].id, [rows])
  const estimateSize = useCallback(
    (i: number) => (rows[i].kind === "header" ? 44 : 40),
    [rows]
  )
  const navigationRef = useRef({ source, selectedIds, rows })
  navigationRef.current = { source, selectedIds, rows }
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => listRef.current,
    estimateSize,
    getItemKey,
    overscan: 8,
  })
  const virtualRows = virtualizer.getVirtualItems()
  const activeDescendant = virtualRows.some(
    (row) => row.index === effectiveActiveIndex
  )
    ? `${listId}-${effectiveActiveIndex}`
    : undefined
  const onToggle = useCallback(
    function onToggle(id: string) {
      const entry = index.byKey.get(id)
      if (!entry || disabled) return
      const navigation = navigationRef.current
      if (navigation.source === "selected" && navigation.selectedIds.has(id)) {
        const options = navigation.rows.filter((row) => row.kind === "tool")
        const position = options.findIndex((row) => row.id === id)
        setActiveId(
          options[position + 1]?.id ?? options[position - 1]?.id ?? null
        )
      } else {
        setActiveId(id)
      }
      setSelection((current) => {
        const field = entry.section === "mcp" ? "mcp" : "actions"
        const next = new Set(current[field])
        if (next.has(entry.key)) next.delete(entry.key)
        else next.add(entry.key)
        return { ...current, [field]: next }
      })
    },
    [index, disabled]
  )

  function toggleGroup(group: ToolGroup) {
    if (disabled) return
    setSelection((current) => {
      const field = group.section === "mcp" ? "mcp" : "actions"
      const next = new Set(current[field])
      const allSelected = group.entries.every((entry) => next.has(entry.key))
      for (const entry of group.entries) {
        if (allSelected) next.delete(entry.key)
        else next.add(entry.key)
      }
      return { ...current, [field]: next }
    })
  }

  function handleKeyDown(event: KeyboardEvent<HTMLElement>) {
    const inList = event.currentTarget === listRef.current
    if (event.nativeEvent.isComposing) return
    if (event.key === "Enter" && query !== deferredQuery) {
      event.preventDefault()
      return
    }
    if (event.key === "Enter" || (event.key === " " && inList)) {
      event.preventDefault()
      const row = rows[effectiveActiveIndex]
      if (row?.kind === "tool") onToggle(row.entry.id)
      return
    }
    let position = optionIndices.indexOf(effectiveActiveIndex)
    const page = Math.max(
      1,
      Math.floor((listRef.current?.clientHeight || 400) / 40)
    )
    switch (event.key) {
      case "ArrowDown":
        position += 1
        break
      case "ArrowUp":
        position -= 1
        break
      case "Home":
        if (!inList) return
        position = 0
        break
      case "End":
        if (!inList) return
        position = optionIndices.length - 1
        break
      case "PageDown":
        position += page
        break
      case "PageUp":
        position -= page
        break
      default:
        return
    }
    event.preventDefault()
    const next =
      optionIndices[Math.max(0, Math.min(position, optionIndices.length - 1))]
    if (next !== undefined) {
      setActiveId(rows[next].id)
      virtualizer.scrollToIndex(next, { align: "auto" })
    }
  }

  function done() {
    if (disabled) return
    const current = getValues()
    const actions = applyToolSelection(
      current.actions,
      selection.actions,
      index.entries
        .filter((entry) => entry.section !== "mcp")
        .map((entry) => entry.key)
    )
    const mcp = applyToolSelection(
      current.mcpIntegrations,
      selection.mcp,
      index.entries
        .filter((entry) => entry.section === "mcp")
        .map((entry) => entry.key)
    )
    const removed = new Set(
      current.actions.filter((key) => !selection.actions.has(key))
    )
    const { toolApprovals } = removeTools(current, removed)
    if (actions !== current.actions)
      setValue("actions", actions, { shouldDirty: true })
    if (mcp !== current.mcpIntegrations)
      setValue("mcpIntegrations", mcp, { shouldDirty: true })
    if (toolApprovals !== current.toolApprovals)
      setValue("toolApprovals", toolApprovals, { shouldDirty: true })
    onOpenChange(false)
  }

  const chooseSource = useCallback(
    function chooseSource(id: string) {
      setSource(id)
      setQuery("")
      setActiveId(null)
      listRef.current?.scrollTo({ top: 0 })
      searchRef.current?.focus()
    },
    [searchRef]
  )

  const railCounts = useMemo(() => {
    const counts = new Map<string, number>()
    let total = 0
    for (const group of index.groups) {
      const selected = group.entries.filter((entry) =>
        selectedIds.has(entry.id)
      ).length
      counts.set(group.id, selected)
      total += selected
    }
    counts.set("selected", total)
    return counts
  }, [index, selectedIds])
  const railRows = useMemo<RailRow[]>(() => {
    const result: RailRow[] = [
      {
        kind: "source",
        id: "all",
        title: "All tools",
        total: index.entries.length,
      },
      { kind: "source", id: "selected", title: "Selected", total: 0 },
    ]
    for (const section of ["mcp", "tracecat", "custom"] as const) {
      const groups = index.groups.filter((group) => group.section === section)
      if (!groups.length) continue
      result.push({
        kind: "label",
        id: section,
        title: {
          mcp: "MCP servers",
          tracecat: "Tracecat registry",
          custom: "Custom registry",
        }[section],
      })
      for (const group of groups)
        result.push({
          kind: "source",
          id: group.id,
          title: group.title,
          entry: group.entries[0],
          total: group.entries.length,
        })
    }
    return result
  }, [index])
  const railRowsRef = useRef(railRows)
  railRowsRef.current = railRows
  const getRailItemKey = useCallback(
    (i: number) => railRowsRef.current[i].id,
    []
  )
  const railVirtualizer = useVirtualizer({
    count: railRows.length,
    getScrollElement: () => railRef.current,
    estimateSize: () => 36,
    getItemKey: getRailItemKey,
    overscan: 8,
  })

  return (
    <>
      <div className="flex h-12 shrink-0 items-center gap-3 border-b px-4">
        <Search className="size-4 text-muted-foreground" />
        <input
          ref={searchRef}
          aria-label="Search tools"
          aria-controls={listId}
          aria-activedescendant={activeDescendant}
          role="combobox"
          aria-expanded
          aria-autocomplete="list"
          value={query}
          onChange={(event) => {
            setQuery(event.target.value)
            setActiveId(null)
            listRef.current?.scrollTo({ top: 0 })
          }}
          onKeyDown={handleKeyDown}
          placeholder={`Search ${index.entries.length.toLocaleString()} tools`}
          className="h-9 min-w-0 flex-1 border-0 bg-transparent text-sm outline-none focus-visible:ring-0"
        />
        <Kbd>esc</Kbd>
      </div>
      <div className="flex min-h-0 flex-1">
        <nav
          ref={railRef}
          aria-label="Tool sources"
          className="w-48 shrink-0 overflow-y-auto border-r py-2 md:w-[280px]"
        >
          <div
            style={{
              height: railVirtualizer.getTotalSize(),
              position: "relative",
            }}
          >
            {railVirtualizer.getVirtualItems().map((virtualRow) => {
              const row = railRows[virtualRow.index]
              return (
                <div
                  key={virtualRow.key}
                  style={{
                    position: "absolute",
                    top: 0,
                    left: 0,
                    width: "100%",
                    height: virtualRow.size,
                    transform: `translateY(${virtualRow.start}px)`,
                  }}
                >
                  <SourceRow
                    row={row}
                    selected={railCounts.get(row.id) ?? 0}
                    active={source === row.id}
                    onChoose={chooseSource}
                  />
                </div>
              )
            })}
          </div>
        </nav>
        <div className="flex min-w-0 flex-1 flex-col">
          {deferredQuery.trim() ? (
            <div className="flex h-11 shrink-0 items-center border-b px-4 text-sm">
              {matches.length} {matches.length === 1 ? "result" : "results"}
            </div>
          ) : null}
          {!deferredQuery.trim() &&
            selectedGroup &&
            selectedGroup.section !== "mcp" && (
              <GroupHeader
                group={selectedGroup}
                selected={selectedIds}
                onToggle={() => toggleGroup(selectedGroup)}
                disabled={disabled}
              />
            )}
          <div
            ref={listRef}
            id={listId}
            role="listbox"
            aria-label="Tools"
            aria-multiselectable="true"
            aria-activedescendant={activeDescendant}
            tabIndex={0}
            onKeyDown={handleKeyDown}
            className={cn(
              "min-h-0 overflow-auto outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring",
              !deferredQuery.trim() && selectedGroup?.section === "mcp"
                ? "h-10 shrink-0"
                : "flex-1"
            )}
          >
            <div
              style={{
                height: virtualizer.getTotalSize(),
                position: "relative",
                width: "100%",
              }}
            >
              {virtualRows.map((virtualRow) => {
                const row = rows[virtualRow.index]
                return (
                  <div
                    key={virtualRow.key}
                    role="presentation"
                    style={{
                      position: "absolute",
                      top: 0,
                      left: 0,
                      width: "100%",
                      height: virtualRow.size,
                      transform: `translateY(${virtualRow.start}px)`,
                    }}
                  >
                    {row.kind === "tool" ? (
                      <ToolOption
                        id={`${listId}-${virtualRow.index}`}
                        entry={row.entry}
                        showIcon={
                          Boolean(deferredQuery.trim()) || source === "selected"
                        }
                        selected={selectedIds.has(row.entry.id)}
                        active={virtualRow.index === effectiveActiveIndex}
                        onToggle={onToggle}
                        disabled={disabled}
                      />
                    ) : (
                      <SectionHeader title={row.title} group={row.group} />
                    )}
                  </div>
                )
              })}
            </div>
            {rows.length === 0 && (
              <p className="p-4 text-sm text-muted-foreground">
                No tools found.
              </p>
            )}
          </div>
          {!deferredQuery.trim() && selectedGroup?.section === "mcp" && (
            <ul aria-label="MCP tools" className="min-h-0 flex-1 overflow-auto">
              {selectedGroup.entries[0].integration?.tools?.map((tool) => (
                <li
                  key={tool.name}
                  className="flex h-10 items-center gap-3 px-4 text-xs"
                >
                  <span className="truncate">{tool.name}</span>
                  <span className="truncate text-muted-foreground">
                    {tool.description}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
      <div className="flex h-14 shrink-0 items-center gap-2 border-t px-4 text-xs">
        <span>
          {maxTools != null
            ? `${count} of ${maxTools} tools`
            : `${count} ${toolLabel} selected`}
          {selection.mcp.size > 0 &&
            ` · ${selection.mcp.size} MCP ${selection.mcp.size === 1 ? "integration" : "integrations"}`}
        </span>
        <div className="ml-auto hidden items-center gap-2 text-muted-foreground sm:flex">
          <Kbd>↑↓</Kbd> Navigate <Kbd>Enter</Kbd> Select
        </div>
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() => onOpenChange(false)}
        >
          Cancel
        </Button>
        <Button type="button" size="sm" disabled={disabled} onClick={done}>
          Done
        </Button>
      </div>
    </>
  )
}

const SourceRow = memo(function SourceRow({
  row,
  selected,
  active,
  onChoose,
}: {
  row: RailRow
  selected: number
  active: boolean
  onChoose: (id: string) => void
}) {
  if (row.kind === "label")
    return (
      <p className="flex h-9 items-end px-4 pb-1 text-xs text-muted-foreground">
        {row.title}
      </p>
    )
  let count: string | number = row.total
  if (row.id === "selected") count = selected
  else if (row.entry && selected) count = `${selected}/${row.total}`
  return (
    <button
      type="button"
      aria-current={active ? "true" : undefined}
      onClick={() => onChoose(row.id)}
      className={cn(
        "flex h-9 w-full items-center gap-2 px-4 text-left text-xs hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring",
        active && "bg-muted"
      )}
    >
      {row.entry && <ToolIcon entry={row.entry} />}
      <span className="min-w-0 flex-1 truncate">{row.title}</span>
      <span
        className={cn(
          "shrink-0 font-mono text-[11px]",
          selected ? "text-foreground" : "text-muted-foreground"
        )}
      >
        {count}
      </span>
    </button>
  )
})

function ToolIcon({
  entry,
  small = false,
}: {
  entry: ToolEntry
  small?: boolean
}) {
  const className = cn("shrink-0 rounded border", small ? "size-5" : "size-6")
  if (entry.integration)
    return (
      <ProviderIcon
        providerId={getMcpProviderIconId(entry.integration.slug)}
        className={className}
      />
    )
  return getIcon(entry.key, { className })
}

function GroupHeader({
  group,
  selected,
  onToggle,
  disabled,
}: {
  group: ToolGroup
  selected: Set<string>
  onToggle: () => void
  disabled?: boolean
}) {
  const count = group.entries.filter((entry) => selected.has(entry.id)).length
  let checked: boolean | "indeterminate" = false
  if (count === group.entries.length) checked = true
  else if (count > 0) checked = "indeterminate"
  return (
    <div className="flex h-11 shrink-0 items-center gap-3 border-b px-4 text-xs">
      <span className="relative flex shrink-0">
        <Checkbox
          className={checked === "indeterminate" ? "[&_svg]:hidden" : undefined}
          aria-label={`Select all in ${group.title}`}
          checked={checked}
          onCheckedChange={onToggle}
          disabled={disabled}
        />
        {checked === "indeterminate" && (
          <Minus
            aria-hidden="true"
            className="pointer-events-none absolute inset-0 m-auto size-3"
          />
        )}
      </span>
      <ToolIcon entry={group.entries[0]} />
      <span className="truncate font-medium">{group.title}</span>
      <span className="min-w-0 flex-1 truncate font-mono text-muted-foreground">
        {group.namespace}
      </span>
      <span className="shrink-0 text-muted-foreground">
        {count} of {group.entries.length} selected
      </span>
    </div>
  )
}

const ToolOption = memo(function ToolOption({
  id,
  entry,
  showIcon,
  selected,
  active,
  onToggle,
  disabled,
}: {
  id: string
  entry: ToolEntry
  showIcon: boolean
  selected: boolean
  active: boolean
  onToggle: (id: string) => void
  disabled?: boolean
}) {
  return (
    <div
      id={id}
      role="option"
      aria-selected={selected}
      aria-disabled={disabled}
      data-selected={active}
      onClick={() => onToggle(entry.id)}
      className={cn(
        "group flex h-10 cursor-pointer items-center gap-3 px-4 text-xs hover:bg-muted/50",
        active && "bg-muted/50"
      )}
    >
      <CheckIndicator checked={selected} className="opacity-100" />
      {showIcon && <ToolIcon entry={entry} small />}
      <span className="w-1/4 min-w-0 truncate">{entry.title}</span>
      <span className="w-1/3 min-w-0 truncate font-mono text-[11px] text-muted-foreground">
        {entry.integration ? entry.integration.slug : entry.key}
      </span>
      <span className="min-w-0 flex-1 truncate text-muted-foreground">
        {entry.description}
      </span>
    </div>
  )
})

const SectionHeader = memo(function SectionHeader({
  title,
  group,
}: {
  title: string
  group?: ToolGroup
}) {
  return (
    <div
      role="presentation"
      className="flex h-11 items-center gap-3 border-b border-border/50 px-4 text-xs"
    >
      {group && <ToolIcon entry={group.entries[0]} />}
      <span className="truncate font-medium">{title}</span>
      <span className="truncate font-mono text-[11px] text-muted-foreground">
        {group?.namespace}
      </span>
    </div>
  )
})
