import { useEffect, useState } from "react"
import {
  ActionResponse,
  actionsGetAction,
  actionsUpdateAction,
  EventHistoryResponse,
  Schedule,
  schedulesCreateSchedule,
  SchedulesCreateScheduleData,
  schedulesDeleteSchedule,
  SchedulesDeleteScheduleData,
  schedulesListSchedules,
  schedulesUpdateSchedule,
  SchedulesUpdateScheduleData,
  UpdateActionParams,
  WorkflowExecutionResponse,
  workflowExecutionsListWorkflowExecutionEventHistory,
  workflowExecutionsListWorkflowExecutions,
} from "@/client"
import { useWorkflowBuilder } from "@/providers/builder"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { CaseEvent, type Case, type WorkflowMetadata } from "@/types/schemas"
import {
  CaseEventParams,
  createCaseEvent,
  fetchCase,
  fetchCaseEvents,
  updateCase,
} from "@/lib/cases"
import { updateWebhook } from "@/lib/trigger"
import { isEmptyObject } from "@/lib/utils"
import { fetchAllPlaybooks, fetchAllWorkflows } from "@/lib/workflow"
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

export type PanelAction = {
  action?: ActionResponse
  isLoading: boolean
  error: Error | null
  mutateAsync: (values: UpdateActionParams) => Promise<ActionResponse>
  queryClient: ReturnType<typeof useQueryClient>
  queryKeys: {
    selectedAction: [string, string, string]
    workflow: [string, string]
  }
}
export function usePanelAction(
  actionId: string,
  workflowId: string
): PanelAction {
  const queryClient = useQueryClient()
  const { setNodes } = useWorkflowBuilder()
  const {
    data: action,
    isLoading,
    error,
  } = useQuery<ActionResponse, Error>({
    queryKey: ["selected_action", actionId, workflowId],
    queryFn: async ({ queryKey }) => {
      const [, actionId, workflowId] = queryKey as [string, string, string]
      return await actionsGetAction({ actionId, workflowId })
    },
  })
  const { mutateAsync } = useMutation({
    mutationFn: async (values: UpdateActionParams) =>
      await actionsUpdateAction({ actionId, requestBody: values }),
    onSuccess: (updatedAction: ActionResponse) => {
      setNodes((nds: UDFNodeType[]) =>
        nds.map((node: UDFNodeType) => {
          if (node.id === actionId) {
            const { title } = updatedAction
            node.data = {
              ...node.data, // Overwrite the existing node data
              title,
              isConfigured:
                updatedAction.inputs !== null ||
                isEmptyObject(updatedAction.inputs),
            }
          }
          return node
        })
      )
      console.log("Action update successful", updatedAction)
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

export function useUpdateWebhook(workflowId: string) {
  const queryClient = useQueryClient()
  const mutation = useMutation({
    mutationFn: async (params: {
      entrypointRef?: string
      method?: "GET" | "POST"
      status?: "online" | "offline"
    }) => await updateWebhook(workflowId, params),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["workflow", workflowId] })
    },
    onError: (error) => {
      console.error("Failed to update webhook:", error)
      toast({
        title: "Error updating webhook",
        description: "Could not update webhook. Please try again.",
        variant: "destructive",
      })
    },
  })

  return mutation
}

export function useWorkflows() {
  const query = useQuery<WorkflowMetadata[], Error>({
    queryKey: ["workflows"],
    queryFn: fetchAllWorkflows,
  })
  return query
}

export function usePlaybooks() {
  const query = useQuery<WorkflowMetadata[], Error>({
    queryKey: ["playbooks"],
    queryFn: fetchAllPlaybooks,
  })
  return query
}

export function useWorkflowExecutions(workflowId: string) {
  const {
    data: workflowExecutions,
    isLoading: workflowExecutionsIsLoading,
    error: workflowExecutionsError,
  } = useQuery<WorkflowExecutionResponse[], Error>({
    queryKey: ["workflow-executions", workflowId],
    queryFn: async () =>
      await workflowExecutionsListWorkflowExecutions({
        workflowId,
      }),
  })
  return {
    workflowExecutions,
    workflowExecutionsIsLoading,
    workflowExecutionsError,
  }
}

export function useWorkflowExecutionEventHistory(workflowExecutionId: string) {
  const {
    data: eventHistory,
    isLoading: eventHistoryLoading,
    error: eventHistoryError,
  } = useQuery<EventHistoryResponse[], Error>({
    queryKey: ["workflow-executions", workflowExecutionId, "event-history"],
    queryFn: async () =>
      await workflowExecutionsListWorkflowExecutionEventHistory({
        executionId: workflowExecutionId,
      }),
  })
  return {
    eventHistory,
    eventHistoryLoading,
    eventHistoryError,
  }
}

export function useSchedules(workflowId: string) {
  const queryClient = useQueryClient()

  // Fetch schedules
  const {
    data: schedules,
    isLoading,
    error,
  } = useQuery<Schedule[], Error>({
    queryKey: [workflowId, "schedules"],
    queryFn: async ({ queryKey }) => {
      const [workflowId] = queryKey as [string, string]
      return await schedulesListSchedules({
        workflowId,
      })
    },
  })

  // Create schedules
  const { mutateAsync: createSchedule } = useMutation({
    mutationFn: async (values: SchedulesCreateScheduleData) =>
      await schedulesCreateSchedule(values),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [workflowId, "schedules"] })
      toast({
        title: "Created schedule",
        description: "Your schedule has been created successfully.",
      })
    },
    onError: (error) => {
      console.error("Failed to create schedule:", error)
      toast({
        title: "Error creating schedule",
        description: "Could not create schedule. Please try again.",
        variant: "destructive",
      })
    },
  })
  // Update schedules
  const { mutateAsync: updateSchedule } = useMutation({
    mutationFn: async (values: SchedulesUpdateScheduleData) =>
      await schedulesUpdateSchedule(values),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [workflowId, "schedules"] })
      toast({
        title: "Updated schedule",
        description: "Your schedule has been updated successfully.",
      })
    },
    onError: (error) => {
      console.error("Failed to update webhook:", error)
      toast({
        title: "Error updating schedule",
        description: "Could not update schedule. Please try again.",
        variant: "destructive",
      })
    },
  })

  // Delete schedule
  const { mutateAsync: deleteSchedule } = useMutation({
    mutationFn: async (values: SchedulesDeleteScheduleData) =>
      await schedulesDeleteSchedule(values),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [workflowId, "schedules"] })
      toast({
        title: "Deleted schedule",
        description: "Your schedule has been deleted successfully.",
      })
    },
    onError: (error) => {
      console.error("Failed to delete schedule:", error)
      toast({
        title: "Error deleting schedule",
        description: "Could not delete schedule. Please try again.",
        variant: "destructive",
      })
    },
  })

  return {
    schedules,
    schedulesIsLoading: isLoading,
    schedulesError: error,
    createSchedule,
    updateSchedule,
    deleteSchedule,
  }
}
