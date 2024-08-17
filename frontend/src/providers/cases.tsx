"use client"

import React, {
  createContext,
  PropsWithChildren,
  useContext,
  useEffect,
  useState,
} from "react"
import { ApiError, CaseResponse, casesListCases } from "@/client"
import { useWorkflow } from "@/providers/workflow"
import { useQuery } from "@tanstack/react-query"

interface CasesContextType {
  cases: CaseResponse[]
}
const CasesContext = createContext<CasesContextType | undefined>(undefined)

export default function CasesProvider({
  children,
}: PropsWithChildren<React.HTMLAttributes<HTMLDivElement>>) {
  const [cases, setCases] = useState<CaseResponse[]>([])
  const { workspaceId, workflowId } = useWorkflow()
  if (!workflowId) {
    console.error(`Non-existent workflow ${workflowId}, cannot load cases`)
    throw new Error("Non-existent workflow, cannot load cases")
  }
  const { data } = useQuery<CaseResponse[], ApiError>({
    queryKey: ["cases"],
    queryFn: async () =>
      await casesListCases({
        workspaceId,
        workflowId,
      }),
  })

  useEffect(() => {
    setCases(data || [])
  }, [data])

  return (
    <CasesContext.Provider value={{ cases }}>{children}</CasesContext.Provider>
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
