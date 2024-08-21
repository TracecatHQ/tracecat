"use client"

import React, {
  createContext,
  PropsWithChildren,
  useContext,
} from "react"
import { ApiError, CaseRead, casesListCases } from "@/client"
import { useWorkspace } from "@/providers/workspace"
import { useQuery } from "@tanstack/react-query"

interface CasesContextType {
  cases: CaseRead[]
  casesLoading: boolean
  casesError: ApiError | null
}
const CasesContext = createContext<CasesContextType | undefined>(undefined)

export default function CasesProvider({
  children,
}: PropsWithChildren<React.HTMLAttributes<HTMLDivElement>>) {
  const { workspaceId } = useWorkspace()
  const {
    data: cases,
    isLoading: casesLoading,
    error: casesError,
  } = useQuery<CaseRead[], ApiError>({
    queryKey: ["cases", workspaceId],
    queryFn: async () =>
      await casesListCases({
        workspaceId,
      }),
  })

  return (
    <CasesContext.Provider
      value={{ cases: cases || [], casesLoading, casesError }}
    >
      {children}
    </CasesContext.Provider>
  )
}

export const useCasesContext = (): CasesContextType => {
  const context = useContext(CasesContext)
  if (context === undefined) {
    throw new Error(
      "useReactFlowInteractions must be used within a ReactFlowInteractionsProvider"
    )
  }
  return context
}
