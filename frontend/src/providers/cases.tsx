"use client"

import React, {
  createContext,
  PropsWithChildren,
  SetStateAction,
  useContext,
  useEffect,
  useState,
} from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { type Case } from "@/types/schemas"
import { getCases, updateCases } from "@/lib/cases"
import { toast } from "@/components/ui/use-toast"

import { useSession } from "./session"
import { useWorkflowMetadata } from "./workflow"

interface CasesContextType {
  cases: Case[]
  setCases: React.Dispatch<SetStateAction<Case[]>>
  isLoading: boolean
  commitCases: () => void
  isCommitable: boolean
  setIsCommitable: React.Dispatch<SetStateAction<boolean>>
  isCommitting: boolean
  setIsCommitting: React.Dispatch<SetStateAction<boolean>>
}
const CasesContext = createContext<CasesContextType | undefined>(undefined)

export default function CasesProvider({
  children,
}: PropsWithChildren<React.HTMLAttributes<HTMLDivElement>>) {
  const session = useSession()
  const [cases, setCases] = useState<Case[]>([])
  const { workflowId } = useWorkflowMetadata()
  const [isCommitable, setIsCommitable] = useState(false)
  const [isCommitting, setIsCommitting] = useState(false)
  const queryClient = useQueryClient()
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

  const { mutateAsync } = useMutation({
    mutationFn: () => updateCases(session, workflowId, cases),
    onSuccess: (data, variables, context) => {
      toast({
        title: "Updated cases",
        description: "successfully committed changes.",
      })
      queryClient.invalidateQueries({ queryKey: ["cases"] })
      setIsCommitable(false)
    },
    onError: (error, variables, context) => {
      console.error("Failed to update cases:", error)
      toast({
        title: "Failed to update cases",
        description: "An error occurred while committing changes to the cases.",
      })
      setIsCommitting(false)
    },
  })

  useEffect(() => {
    setCases(data || [])
  }, [data])

  const commitChanges = async () => {
    setIsCommitting(() => true)
    try {
      await mutateAsync()
    } finally {
      setIsCommitting(() => false)
    }
  }

  return (
    <CasesContext.Provider
      value={{
        cases,
        setCases,
        isLoading,
        commitCases: commitChanges,
        isCommitable,
        setIsCommitable,
        isCommitting,
        setIsCommitting,
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
