"use client"

import React, {
  createContext,
  PropsWithChildren,
  SetStateAction,
  useContext,
  useEffect,
  useState,
} from "react"
import { useQuery } from "@tanstack/react-query"

import { type Case } from "@/types/schemas"
import { getCases } from "@/lib/cases"

import { useSession } from "./session"
import { useWorkflowMetadata } from "./workflow"

interface CasesContextType {
  cases: Case[]
  setCases: React.Dispatch<SetStateAction<Case[]>>
  isLoading: boolean
}
const CasesContext = createContext<CasesContextType | undefined>(undefined)

export default function CasesProvider({
  children,
}: PropsWithChildren<React.HTMLAttributes<HTMLDivElement>>) {
  const session = useSession()
  const [cases, setCases] = useState<Case[]>([])
  const { workflowId } = useWorkflowMetadata()
  if (!workflowId) {
    console.error(`Non-existent workflow ${workflowId}, cannot load cases`)
    throw new Error("Non-existent workflow, cannot load cases")
  }
  const { data, isLoading } = useQuery<Case[], Error>({
    queryKey: ["cases"],
    queryFn: async () => {
      const cases = await getCases(session, workflowId)
      return cases
    },
  })

  useEffect(() => {
    setCases(data || [])
  }, [data])

  return (
    <CasesContext.Provider
      value={{
        cases,
        setCases,
        isLoading,
      }}
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
