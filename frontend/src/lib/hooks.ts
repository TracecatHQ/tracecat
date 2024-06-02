import { useEffect, useState } from "react"
import { useWorkflowBuilder } from "@/providers/builder"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { Action, CaseEvent, type Case } from "@/types/schemas"
import {
  CaseEventParams,
  createCaseEvent,
  fetchCase,
  fetchCaseEvents,
  updateCase,
} from "@/lib/cases"
import { getActionById, updateAction } from "@/lib/workflow"
import { toast } from "@/components/ui/use-toast"
import { UDFNodeType } from "@/components/workspace/canvas/udf-node"

export function useLocalStorage<T>(
  key: string,
  defaultValue: T
): [T, (value: T) => void] {
  const [value, setValue] = useState<T>(() => {
    if (typeof window === "undefined") {
      return defaultValue
    }
    const storedValue = localStorage.getItem(key)
    return storedValue ? JSON.parse(storedValue) : defaultValue
  })
  useEffect(() => {
    localStorage.setItem(key, JSON.stringify(value))
  }, [key, value])
  return [value, setValue]
}

export function usePanelCase(workflowId: string, caseId: string) {
  const queryClient = useQueryClient()
  const { data, isLoading, error } = useQuery<Case, Error>({
    queryKey: ["case", caseId],
    queryFn: async () => await fetchCase(workflowId, caseId),
  })
  const { mutateAsync } = useMutation({
    mutationFn: (newCase: Case) => updateCase(workflowId, caseId, newCase),
    onSuccess: () => {
      toast({
        title: "Updated case",
        description: "Your case has been updated successfully.",
      })
      queryClient.invalidateQueries({
        queryKey: ["case", caseId],
      })
      queryClient.invalidateQueries({
        queryKey: ["cases"],
      })
    },
    onError: (error) => {
      console.error("Failed to update action:", error)
      toast({
        title: "Failed to save action",
        description: "Could not update your action. Please try again.",
      })
    },
  })

  return {
    caseData: data,
    caseIsLoading: isLoading,
    caseError: error,
    mutateCaseAsync: mutateAsync,
  }
}

export function useCaseEvents(workflowId: string, caseId: string) {
  const queryClient = useQueryClient()
  const { data, isLoading, error } = useQuery<CaseEvent[], Error>({
    queryKey: ["caseEvents", caseId],
    queryFn: async () => {
      return await fetchCaseEvents(workflowId, caseId)
    },
  })

  const { mutateAsync } = useMutation({
    mutationFn: async (newEvent: CaseEventParams) => {
      await createCaseEvent(workflowId, caseId, newEvent)
    },
    onSuccess: () => {
      console.log("Case event created")
      toast({
        title: "Created case event",
        description: "Your case event has been created successfully.",
      })
      queryClient.invalidateQueries({
        queryKey: ["caseEvents", caseId],
      })
      queryClient.invalidateQueries({
        queryKey: ["case", caseId],
      })
    },
    onError: (error) => {
      console.error("Failed to create case event:", error)
      toast({
        title: "Failed to create case event",
        description: "Could not create case event. Please try again.",
      })
    },
  })

  return {
    caseEvents: data,
    caseEventsIsLoading: isLoading,
    caseEventsError: error,
    mutateCaseEventsAsync: mutateAsync,
  }
}

export type PanelAction<T extends Record<string, any>> = {
  action?: Action
  isLoading: boolean
  error: Error | null
  mutateAsync: (values: T) => Promise<any>
  queryClient: ReturnType<typeof useQueryClient>
  queryKeys: {
    selectedAction: [string, string, string]
    workflow: [string, string]
  }
}
export function usePanelAction<T extends Record<string, any>>(
  actionId: string,
  workflowId: string
): PanelAction<T> {
  const queryClient = useQueryClient()
  const { setNodes } = useWorkflowBuilder()
  const {
    data: action,
    isLoading,
    error,
  } = useQuery<Action, Error>({
    queryKey: ["selected_action", actionId, workflowId],
    queryFn: async ({ queryKey }) => {
      const [, actionId, workflowId] = queryKey as [string, string, string]
      return await getActionById(actionId, workflowId)
    },
  })
  const { mutateAsync } = useMutation({
    mutationFn: (values: T) => updateAction(actionId, values),
    onSuccess: (data: Action) => {
      setNodes((nds: UDFNodeType[]) =>
        nds.map((node: UDFNodeType) => {
          if (node.id === actionId) {
            const { title } = data
            node.data = {
              ...node.data, // Overwrite the existing node data
              title,
              isConfigured: data.inputs !== null,
            }
          }
          return node
        })
      )
      console.log("Action update successful", data)
      toast({
        title: "Saved action",
        description: "Your action has been updated successfully.",
      })
      queryClient.invalidateQueries({
        queryKey: ["selected_action", actionId, workflowId],
      })
      queryClient.invalidateQueries({
        queryKey: ["workflow", workflowId],
      })
    },
    onError: (error) => {
      console.error("Failed to update action:", error)
      toast({
        title: "Failed to save action",
        description: "Could not update your action. Please try again.",
      })
    },
  })
  return {
    action,
    isLoading,
    error,
    mutateAsync,
    queryClient,
    queryKeys: {
      selectedAction: ["selected_action", actionId, workflowId],
      workflow: ["workflow", workflowId],
    },
  }
}

export function useActionInputs(action?: Action) {
  const [actionInputs, setActionInputs] = useState<Record<string, any>>(
    action?.inputs || {}
  )

  useEffect(() => {
    if (action?.inputs) {
      setActionInputs(action.inputs)
    }
  }, [action?.inputs])

  return { actionInputs, setActionInputs }
}
