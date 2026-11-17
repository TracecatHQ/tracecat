"use client"

import { MagnifyingGlassIcon } from "@radix-ui/react-icons"
import { useVirtualizer } from "@tanstack/react-virtual"
import fuzzysort from "fuzzysort"
import {
  type KeyboardEvent,
  type ReactNode,
  type RefObject,
  useCallback,
  useDeferredValue,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react"
import type { RegistryActionReadMinimal } from "@/client"
import { cn } from "@/lib/utils"

export type ActionSearchKey = keyof RegistryActionReadMinimal

export type ActionSearchResult = {
  action: RegistryActionReadMinimal
  /** Per-key match results, aligned with the `keys` passed to the search. */
  matches: ReadonlyArray<Fuzzysort.Result | null>
}

/**
 * Fuzzy-search registry actions, keeping the input order when the query is
 * empty and relevance order otherwise.
 */
export function searchActions(
  actions: readonly RegistryActionReadMinimal[],
  query: string,
  keys: readonly ActionSearchKey[]
): ActionSearchResult[] {
  const normalized = query.trim()
  if (!normalized) {
    return actions.map((action) => ({ action, matches: [] }))
  }
  return fuzzysort
    .go(normalized, actions, { keys: keys as string[], all: false })
    .map((result) => ({
      action: result.obj,
      matches: keys.map((_, i) => result[i] ?? null),
    }))
}

/** Search with a deferred query so typing never waits on the list. */
export function useActionSearch(
  actions: readonly RegistryActionReadMinimal[] | undefined,
  query: string,
  keys: readonly ActionSearchKey[]
) {
  const deferredQuery = useDeferredValue(query)
  const keysId = keys.join("\0")
  const results = useMemo(
    () => searchActions(actions ?? [], deferredQuery, keys),
    [actions, deferredQuery, keysId]
  )
  return { results, isStale: deferredQuery !== query }
}

/** Render text with fuzzysort match indexes in bold, without innerHTML. */
export function HighlightedText({
  result,
  text,
}: {
  result: Fuzzysort.Result | null | undefined
  text: string
}) {
  if (!result?.indexes?.length) {
    return <>{text}</>
  }
  const sorted = [...result.indexes].sort((a, b) => a - b)
  const segments: ReactNode[] = []
  let lastEnd = 0
  for (const idx of sorted) {
    if (idx > lastEnd) segments.push(text.slice(lastEnd, idx))
    segments.push(<b key={idx}>{text[idx]}</b>)
    lastEnd = idx + 1
  }
  if (lastEnd < text.length) segments.push(text.slice(lastEnd))
  return <>{segments}</>
}

export interface ActionListboxProps {
  results: ActionSearchResult[]
  onSelect: (action: RegistryActionReadMinimal) => void
  renderItem: (result: ActionSearchResult, active: boolean) => ReactNode
  /** Controlled search value; omit `onQueryChange` to hide the input. */
  query?: string
  onQueryChange?: (value: string) => void
  placeholder?: string
  heading?: ReactNode
  empty?: ReactNode
  /** Estimated row height in px; rows are measured after mount. */
  estimateSize?: number
  /** Maximum list height in px; the list shrinks to fit fewer rows. */
  maxHeight?: number
  /** Fill the parent's remaining height instead of using `maxHeight`. */
  fill?: boolean
  autoFocus?: boolean
  inputRef?: RefObject<HTMLInputElement>
  inputClassName?: string
  className?: string
  /** Accessible label for the listbox. */
  label?: string
}

/**
 * Searchable, virtualized single-select list of registry actions.
 *
 * Only the visible rows are mounted, so the cost of opening, typing and
 * scrolling stays flat as the registry grows past thousands of actions.
 */
export function ActionListbox({
  results,
  onSelect,
  renderItem,
  query,
  onQueryChange,
  placeholder = "Search actions...",
  heading,
  empty = "No actions found.",
  estimateSize = 48,
  maxHeight = 300,
  fill = false,
  autoFocus = false,
  inputRef,
  inputClassName,
  className,
  label = "Actions",
}: ActionListboxProps) {
  const listRef = useRef<HTMLDivElement>(null)
  const listId = useId()
  const [activeIndex, setActiveIndex] = useState(0)
  const hasInput = onQueryChange !== undefined

  const virtualizer = useVirtualizer({
    count: results.length,
    getScrollElement: () => listRef.current,
    estimateSize: () => estimateSize,
    getItemKey: (i) => results[i]?.action.action ?? i,
    overscan: 6,
  })

  // Results change with the query: go back to the top, first row active.
  useEffect(() => {
    setActiveIndex(0)
    virtualizer.scrollToOffset(0)
  }, [results])

  const moveTo = useCallback(
    (index: number) => {
      if (!results.length) return
      const next = Math.max(0, Math.min(index, results.length - 1))
      setActiveIndex(next)
      virtualizer.scrollToIndex(next, { align: "auto" })
    },
    [results.length, virtualizer]
  )

  function handleKeyDown(event: KeyboardEvent<HTMLElement>) {
    if (event.nativeEvent.isComposing) return
    const page = Math.max(
      1,
      Math.floor((listRef.current?.clientHeight || maxHeight) / estimateSize)
    )
    switch (event.key) {
      case "ArrowDown":
        moveTo(activeIndex + 1)
        break
      case "ArrowUp":
        moveTo(activeIndex - 1)
        break
      case "PageDown":
        moveTo(activeIndex + page)
        break
      case "PageUp":
        moveTo(activeIndex - page)
        break
      case "Home":
        if (hasInput) return
        moveTo(0)
        break
      case "End":
        if (hasInput) return
        moveTo(results.length - 1)
        break
      case "Enter": {
        const result = results[activeIndex]
        if (result) onSelect(result.action)
        break
      }
      default:
        return
    }
    event.preventDefault()
  }

  const virtualItems = virtualizer.getVirtualItems()
  const totalSize = virtualizer.getTotalSize()
  const activeDescendant =
    results.length > 0 ? `${listId}-${activeIndex}` : undefined

  return (
    <div
      className={cn("flex min-h-0 flex-col", fill && "flex-1", className)}
      onKeyDown={hasInput ? undefined : handleKeyDown}
    >
      {hasInput && (
        <div className="flex items-center border-b px-3">
          <MagnifyingGlassIcon className="mr-2 size-4 shrink-0 opacity-50" />
          <input
            ref={inputRef}
            role="combobox"
            aria-expanded
            aria-controls={listId}
            aria-activedescendant={activeDescendant}
            aria-autocomplete="list"
            autoFocus={autoFocus}
            value={query ?? ""}
            onChange={(e) => onQueryChange(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={placeholder}
            className={cn(
              "flex h-10 w-full rounded-md bg-transparent py-3 text-sm outline-none placeholder:text-muted-foreground",
              inputClassName
            )}
          />
        </div>
      )}
      {heading && results.length > 0 && (
        <div className="px-3 pb-1 pt-2 text-xs font-medium text-muted-foreground">
          {heading}
        </div>
      )}
      {results.length === 0 ? (
        <div className="py-6 text-center text-xs text-muted-foreground">
          {empty}
        </div>
      ) : (
        <div
          ref={listRef}
          id={listId}
          role="listbox"
          aria-label={label}
          aria-activedescendant={hasInput ? undefined : activeDescendant}
          tabIndex={hasInput ? -1 : 0}
          className={cn(
            "no-scrollbar overflow-y-auto overflow-x-hidden px-1 pb-1 outline-none",
            fill && "min-h-0 flex-1"
          )}
          style={
            fill ? undefined : { height: Math.min(totalSize + 4, maxHeight) }
          }
        >
          <div className="relative w-full" style={{ height: totalSize }}>
            {virtualItems.map((item) => {
              const result = results[item.index]
              const active = item.index === activeIndex
              return (
                <div
                  key={item.key}
                  id={`${listId}-${item.index}`}
                  role="option"
                  aria-selected={active}
                  data-index={item.index}
                  data-active={active || undefined}
                  ref={virtualizer.measureElement}
                  onMouseMove={() => {
                    if (!active) setActiveIndex(item.index)
                  }}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => onSelect(result.action)}
                  className={cn(
                    "absolute left-0 top-0 w-full cursor-pointer select-none rounded-sm px-2 outline-none",
                    active && "bg-accent text-accent-foreground"
                  )}
                  style={{ transform: `translateY(${item.start}px)` }}
                >
                  {renderItem(result, active)}
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
