"use client"

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
} from "react"
import type { CaseUpdate } from "@/client"

type CaseSelectionState = {
  selectedCount: number
  selectedCaseIds: string[]
  clearSelection?: () => void
  deleteSelected?: () => Promise<void>
  bulkUpdateSelectedCases?: (
    updates: Partial<CaseUpdate>,
    options?: {
      successTitle?: string
      successDescription?: string
    }
  ) => Promise<void>
  /** Group the selection under `parent`, or return it to the top level with `null`. */
  setParentForSelectedCases?: (
    parent: { id: string; short_id: string } | null
  ) => Promise<void>
  isDeleting?: boolean
  isUpdating?: boolean
  isChangingParent?: boolean
}

type CaseSelectionContextValue = CaseSelectionState & {
  updateSelection: (state: Partial<CaseSelectionState>) => void
  resetSelection: () => void
}

const initialState: CaseSelectionState = {
  selectedCount: 0,
  selectedCaseIds: [],
  isDeleting: false,
  isUpdating: false,
}

const defaultContextValue: CaseSelectionContextValue = {
  ...initialState,
  updateSelection: () => {},
  resetSelection: () => {},
}

const CaseSelectionContext =
  createContext<CaseSelectionContextValue>(defaultContextValue)

export function CaseSelectionProvider({ children }: React.PropsWithChildren) {
  const [state, setState] = useState<CaseSelectionState>(initialState)

  const updateSelection = useCallback((next: Partial<CaseSelectionState>) => {
    setState((prev) => ({ ...prev, ...next }))
  }, [])

  const resetSelection = useCallback(() => {
    setState(() => ({ ...initialState }))
  }, [])

  const value = useMemo<CaseSelectionContextValue>(
    () => ({
      ...state,
      updateSelection,
      resetSelection,
    }),
    [state, updateSelection, resetSelection]
  )

  return (
    <CaseSelectionContext.Provider value={value}>
      {children}
    </CaseSelectionContext.Provider>
  )
}

export function useCaseSelection() {
  return useContext(CaseSelectionContext)
}
