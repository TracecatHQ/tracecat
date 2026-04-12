/**
 * @jest-environment jsdom
 */

import { act, renderHook, waitFor } from "@testing-library/react"
import { useCaseColumnVisibility } from "@/hooks/use-case-column-visibility"

const STORAGE_KEY = "cases-visible-columns:v1"

function getStorageKey(workspaceId: string): string {
  return `${workspaceId}:${STORAGE_KEY}`
}

describe("useCaseColumnVisibility", () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  it("prunes stale IDs and preserves the max-4 cap after definitions load", async () => {
    window.localStorage.setItem(
      getStorageKey("workspace-1"),
      JSON.stringify([
        "field:stale1",
        "field:stale2",
        "field:stale3",
        "field:stale4",
      ])
    )

    type KnownColumnProps = {
      knownColumnIds: Set<string> | undefined
    }

    const { result, rerender } = renderHook(
      ({ knownColumnIds }: KnownColumnProps) =>
        useCaseColumnVisibility("workspace-1", knownColumnIds),
      {
        initialProps: { knownColumnIds: undefined } as KnownColumnProps,
      }
    )

    expect(result.current.visibleColumnIds).toEqual([
      "field:stale1",
      "field:stale2",
      "field:stale3",
      "field:stale4",
    ])

    rerender({
      knownColumnIds: new Set([
        "field:new1",
        "field:new2",
        "field:new3",
        "field:new4",
      ]),
    })

    await waitFor(() => {
      expect(result.current.visibleColumnIds).toEqual([])
    })

    act(() => {
      result.current.toggleColumn("field:new1")
      result.current.toggleColumn("field:new2")
      result.current.toggleColumn("field:new3")
      result.current.toggleColumn("field:new4")
      result.current.toggleColumn("field:new5")
    })

    expect(result.current.visibleColumnIds).toEqual([
      "field:new1",
      "field:new2",
      "field:new3",
      "field:new4",
    ])
    expect(
      JSON.parse(
        window.localStorage.getItem(getStorageKey("workspace-1")) ?? "[]"
      )
    ).toEqual(["field:new1", "field:new2", "field:new3", "field:new4"])
  })

  it("does not truncate persisted IDs before known columns load", async () => {
    window.localStorage.setItem(
      getStorageKey("workspace-1"),
      JSON.stringify([
        "field:stale1",
        "field:stale2",
        "field:stale3",
        "field:stale4",
        "field:valid1",
        "field:valid2",
      ])
    )

    type KnownColumnProps = {
      knownColumnIds: Set<string> | undefined
    }

    const { result, rerender } = renderHook(
      ({ knownColumnIds }: KnownColumnProps) =>
        useCaseColumnVisibility("workspace-1", knownColumnIds),
      {
        initialProps: { knownColumnIds: undefined } as KnownColumnProps,
      }
    )

    expect(result.current.visibleColumnIds).toEqual([
      "field:stale1",
      "field:stale2",
      "field:stale3",
      "field:stale4",
      "field:valid1",
      "field:valid2",
    ])

    await waitFor(() => {
      expect(
        JSON.parse(
          window.localStorage.getItem(getStorageKey("workspace-1")) ?? "[]"
        )
      ).toEqual([
        "field:stale1",
        "field:stale2",
        "field:stale3",
        "field:stale4",
        "field:valid1",
        "field:valid2",
      ])
    })

    rerender({
      knownColumnIds: new Set(["field:valid1", "field:valid2"]),
    })

    await waitFor(() => {
      expect(result.current.visibleColumnIds).toEqual([
        "field:valid1",
        "field:valid2",
      ])
    })
  })

  it("reloads normalized columns when the workspace changes", async () => {
    window.localStorage.setItem(
      getStorageKey("workspace-1"),
      JSON.stringify([
        "field:alpha",
        "field:alpha",
        "field:stale",
        "field:beta",
      ])
    )
    window.localStorage.setItem(
      getStorageKey("workspace-2"),
      JSON.stringify([
        "field:gamma",
        "field:stale",
        "field:delta",
        "field:gamma",
      ])
    )

    const knownColumnIds = new Set([
      "field:alpha",
      "field:beta",
      "field:gamma",
      "field:delta",
    ])

    const { result, rerender } = renderHook(
      ({ workspaceId }: { workspaceId: string }) =>
        useCaseColumnVisibility(workspaceId, knownColumnIds),
      {
        initialProps: { workspaceId: "workspace-1" },
      }
    )

    expect(result.current.visibleColumnIds).toEqual([
      "field:alpha",
      "field:beta",
    ])

    rerender({ workspaceId: "workspace-2" })

    await waitFor(() => {
      expect(result.current.visibleColumnIds).toEqual([
        "field:gamma",
        "field:delta",
      ])
    })
  })
})
