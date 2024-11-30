import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import {
  ActionRead,
  actionsGetAction,
  actionsUpdateAction,
  ActionUpdate,
  ApiError,
  CreateWorkspaceParams,
  EventHistoryResponse,
  organizationDeleteOrgMember,
  OrganizationDeleteOrgMemberData,
  organizationListOrgMembers,
  organizationUpdateOrgMember,
  OrganizationUpdateOrgMemberData,
  OrgMemberRead,
  RegistryActionCreate,
  RegistryActionRead,
  registryActionsCreateRegistryAction,
  registryActionsDeleteRegistryAction,
  RegistryActionsDeleteRegistryActionData,
  registryActionsGetRegistryAction,
  registryActionsListRegistryActions,
  registryActionsUpdateRegistryAction,
  RegistryActionsUpdateRegistryActionData,
  registryRepositoriesDeleteRegistryRepository,
  RegistryRepositoriesDeleteRegistryRepositoryData,
  registryRepositoriesListRegistryRepositories,
  registryRepositoriesSyncRegistryRepositories,
  RegistryRepositoriesSyncRegistryRepositoriesData,
  RegistryRepositoryReadMinimal,
  Schedule,
  schedulesCreateSchedule,
  SchedulesCreateScheduleData,
  schedulesDeleteSchedule,
  SchedulesDeleteScheduleData,
  schedulesListSchedules,
  schedulesUpdateSchedule,
  SchedulesUpdateScheduleData,
  SecretCreate,
  SecretReadMinimal,
  secretsCreateSecret,
  secretsDeleteSecretById,
  secretsListSecrets,
  secretsUpdateSecretById,
  SecretUpdate,
  triggersUpdateWebhook,
  UpsertWebhookParams,
  usersUsersPatchCurrentUser,
  UserUpdate,
  WorkflowExecutionResponse,
  workflowExecutionsListWorkflowExecutionEventHistory,
  workflowExecutionsListWorkflowExecutions,
  WorkflowMetadataResponse,
  workflowsCreateWorkflow,
  WorkflowsCreateWorkflowData,
  workflowsDeleteWorkflow,
  workflowsListWorkflows,
  WorkspaceMember,
  workspacesCreateWorkspace,
  workspacesDeleteWorkspace,
  workspacesListWorkspaces,
} from "@/client"
import { useWorkspace } from "@/providers/workspace"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import Cookies from "js-cookie"

import { retryHandler, TracecatApiError } from "@/lib/errors"
import { toast } from "@/components/ui/use-toast"

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

export function useAction(
  actionId: string,
  workspaceId: string,
  workflowId: string
) {
  const [isSaving, setIsSaving] = useState(false)
  const queryClient = useQueryClient()
  const {
    data: action,
    isLoading: actionIsLoading,
    error: actionError,
  } = useQuery<ActionRead, Error>({
    queryKey: ["action", actionId, workflowId],
    queryFn: async ({ queryKey }) => {
      const [, actionId, workflowId] = queryKey as [string, string, string]
      return await actionsGetAction({ workspaceId, actionId, workflowId })
    },
  })
  const { mutateAsync: updateAction } = useMutation({
    mutationFn: async (values: ActionUpdate) => {
      setIsSaving(true)
      return await actionsUpdateAction({
        workspaceId,
        actionId,
        requestBody: values,
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["action"],
      })
      queryClient.invalidateQueries({
        queryKey: ["workflow", workflowId],
      })
      // Add a small delay before clearing the saving state to show feedback
      setTimeout(() => {
        setIsSaving(false)
      }, 1000)
    },
    onError: (error) => {
      console.error("Failed to update action:", error)
      toast({
        title: "Failed to save action",
        description: "Could not update your action. Please try again.",
      })
      setIsSaving(false)
    },
  })
  return {
    action,
    actionIsLoading,
    actionError,
    updateAction,
    isSaving,
  }
}

export function useUpdateWebhook(workspaceId: string, workflowId: string) {
  const queryClient = useQueryClient()
  const mutation = useMutation({
    mutationFn: async (params: UpsertWebhookParams) =>
      await triggersUpdateWebhook({
        workspaceId,
        workflowId,
        requestBody: params,
      }),
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

export function useWorkflowManager() {
  const queryClient = useQueryClient()
  const { workspaceId } = useWorkspace()

  // List workflows
  const {
    data: workflows,
    isLoading: workflowsLoading,
    error: workflowsError,
  } = useQuery<WorkflowMetadataResponse[], ApiError>({
    queryKey: ["workflows"],
    queryFn: async () => await workflowsListWorkflows({ workspaceId }),
    retry: retryHandler,
  })

  // Create workflow
  const { mutateAsync: createWorkflow } = useMutation({
    mutationFn: async (params: WorkflowsCreateWorkflowData) =>
      await workflowsCreateWorkflow(params),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["workflows", workspaceId] })
      toast({
        title: "Created workflow",
        description: "Your workflow has been created successfully.",
      })
    },
    onError: (error: TracecatApiError) => {
      switch (error.status) {
        case 400:
          toast({
            title: "Cannot create workflow",
            description: "The uploaded workflow YAML / JSON is invalid.",
          })
          break
        case 409:
          toast({
            title: "Workflow already exists",
            description: "A workflow with the same ID already exists.",
          })
          break
        default:
          console.error("Failed to create workflow:", error)
          toast({
            title: "Error creating workflow",
            description: error.body.detail + ". Please try again.",
            variant: "destructive",
          })
      }
    },
  })

  // Delete workflow
  const { mutateAsync: deleteWorkflow } = useMutation({
    mutationFn: async (workflowId: string) =>
      await workflowsDeleteWorkflow({
        workflowId,
        workspaceId,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["workflows"] })
      toast({
        title: "Deleted workflow",
        description: "Your workflow has been deleted successfully.",
      })
    },
    onError: (error: TracecatApiError) => {
      switch (error.status) {
        case 400:
          toast({
            title: "Cannot delete workflow",
            description: String(error.body.detail),
          })
          break
        default:
          console.error("Failed to delete workflow:", error)
          toast({
            title: "Error deleting workflow",
            description: error.body.detail + ". Please try again.",
            variant: "destructive",
          })
      }
    },
  })
  return {
    workflows,
    workflowsLoading,
    workflowsError,
    createWorkflow,
    deleteWorkflow,
  }
}

export function useWorkspaceManager() {
  const queryClient = useQueryClient()
  const router = useRouter()

  // List workspaces
  const {
    data: workspaces,
    error: workspacesError,
    isLoading: workspacesLoading,
  } = useQuery({
    queryKey: ["workspaces"],
    queryFn: async () => await workspacesListWorkspaces(),
  })

  // Create workspace
  const { mutateAsync: createWorkspace } = useMutation({
    mutationFn: async (params: CreateWorkspaceParams) =>
      await workspacesCreateWorkspace({
        requestBody: params,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["workspaces"] })
      toast({
        title: "Created workspace",
        description: "Your workspace has been created successfully.",
      })
    },
    onError: (error: TracecatApiError) => {
      switch (error.status) {
        case 409:
          console.log(
            "Workspace with this name already exists.",
            error.body.detail
          )
          break
        default:
          console.error("Failed to create workspace:", error)
          toast({
            title: "Error creating workspace",
            description: error.body.detail + ". Please try again.",
          })
      }
    },
  })

  // Delete workspace
  const { mutateAsync: deleteWorkspace } = useMutation({
    mutationFn: async (workspaceId: string) =>
      await workspacesDeleteWorkspace({
        workspaceId,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["workspaces"] })
      router.replace("/workspaces")
      toast({
        title: "Deleted workspace",
        description: "Your workspace has been deleted successfully.",
      })
    },
    onError: (error: TracecatApiError) => {
      switch (error.status) {
        case 400:
          toast({
            title: "Cannot delete workspace",
            description: JSON.stringify(error.body.detail),
          })
          break
        default:
          console.error("Failed to delete workspace:", error)
          toast({
            title: "Error deleting workspace",
            description: error.body.detail + ". Please try again.",
            variant: "destructive",
          })
      }
    },
  })

  // Cookies
  const getLastWorkspaceId = () =>
    Cookies.get("__tracecat:workspaces:last-viewed")
  const setLastWorkspaceId = (id?: string) =>
    Cookies.set("__tracecat:workspaces:last-viewed", id ?? "")
  const clearLastWorkspaceId = () =>
    Cookies.remove("__tracecat:workspaces:last-viewed")

  return {
    workspaces,
    workspacesError,
    workspacesLoading,
    createWorkspace,
    deleteWorkspace,
    getLastWorkspaceId,
    setLastWorkspaceId,
    clearLastWorkspaceId,
  }
}

export function useWorkflowExecutions(
  workflowId: string,
  options?: {
    refetchInterval?: number
  }
) {
  const { workspaceId } = useWorkspace()
  const {
    data: workflowExecutions,
    isLoading: workflowExecutionsIsLoading,
    error: workflowExecutionsError,
  } = useQuery<WorkflowExecutionResponse[], Error>({
    queryKey: ["workflow-executions", workflowId],
    queryFn: async () =>
      await workflowExecutionsListWorkflowExecutions({
        workspaceId,
        workflowId,
      }),
    ...options,
  })
  return {
    workflowExecutions,
    workflowExecutionsIsLoading,
    workflowExecutionsError,
  }
}

export function useWorkflowExecutionEventHistory(workflowExecutionId: string) {
  const { workspaceId } = useWorkspace()
  const {
    data: eventHistory,
    isLoading: eventHistoryLoading,
    error: eventHistoryError,
  } = useQuery<EventHistoryResponse[], Error>({
    queryKey: ["workflow-executions", workflowExecutionId, "event-history"],
    queryFn: async () =>
      await workflowExecutionsListWorkflowExecutionEventHistory({
        workspaceId,
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
  const { workspaceId } = useWorkspace()
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
        workspaceId,
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

export function useWorkspaceSecrets() {
  const queryClient = useQueryClient()
  const { workspaceId } = useWorkspace()
  const {
    data: secrets,
    isLoading: secretsIsLoading,
    error: secretsError,
  } = useQuery<SecretReadMinimal[], ApiError>({
    queryKey: ["workspace-secrets"],
    queryFn: async () =>
      await secretsListSecrets({
        workspaceId,
        level: "workspace",
        type: ["custom"],
      }),
  })

  // Create secret
  const { mutateAsync: createSecret } = useMutation({
    mutationFn: async (secret: SecretCreate) =>
      await secretsCreateSecret({
        workspaceId,
        requestBody: secret,
      }),
    onSuccess: () => {
      toast({
        title: "Added new secret",
        description: "New secret added successfully.",
      })
      queryClient.invalidateQueries({ queryKey: ["workspace-secrets"] })
    },
    onError: (error: TracecatApiError) => {
      switch (error.status) {
        case 409:
          console.error("Secret already exists", error)
          toast({
            title: "Secret already exists",
            description:
              "Secrets with the same name and environment are not supported.",
          })
          break
        default:
          console.error("Failed to create secret", error)
          toast({
            title: "Failed to add new secret",
            description: "Please contact support for help.",
          })
      }
    },
  })

  // Update secret
  const { mutateAsync: updateSecretById } = useMutation({
    mutationFn: async ({
      secretId,
      params,
    }: {
      secretId: string
      params: SecretUpdate
    }) =>
      await secretsUpdateSecretById({
        workspaceId,
        secretId,
        requestBody: params,
      }),
    onSuccess: () => {
      toast({
        title: "Updated secret",
        description: "Secret updated successfully.",
      })
      queryClient.invalidateQueries({ queryKey: ["workspace-secrets"] })
    },
    onError: (error) => {
      console.error("Failed to update secret", error)
      toast({
        title: "Failed to update secret",
        description: "An error occurred while updating the secret.",
      })
    },
  })

  // Delete secret
  const { mutateAsync: deleteSecretById } = useMutation({
    mutationFn: async (secret: SecretReadMinimal) =>
      await secretsDeleteSecretById({ workspaceId, secretId: secret.id }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["workspace-secrets"] })
      toast({
        title: "Deleted secret",
        description: "Secret deleted successfully.",
      })
    },
    onError: (error) => {
      console.error("Failed to delete credentials", error)
      toast({
        title: "Failed to delete secret",
        description: "An error occurred while deleting the secret.",
      })
    },
  })

  return {
    secrets,
    secretsIsLoading,
    secretsError,
    createSecret,
    updateSecretById,
    deleteSecretById,
  }
}

export function useOrgSecrets() {
  const queryClient = useQueryClient()
  // list custom secrets
  const {
    data: orgSecrets,
    isLoading: orgSecretsIsLoading,
    error: orgSecretsError,
  } = useQuery<SecretReadMinimal[]>({
    queryKey: ["org-custom-secrets"],
    queryFn: async () =>
      await secretsListSecrets({
        level: "organization",
        type: ["custom"],
      }),
  })

  // list ssh keys
  const {
    data: orgSSHKeys,
    isLoading: orgSSHKeysIsLoading,
    error: orgSSHKeysError,
  } = useQuery<SecretReadMinimal[]>({
    queryKey: ["org-ssh-keys"],
    queryFn: async () =>
      await secretsListSecrets({
        level: "organization",
        type: ["ssh-key"],
      }),
  })

  // create
  const { mutateAsync: createSecret } = useMutation({
    mutationFn: async (params: SecretCreate) =>
      await secretsCreateSecret({ requestBody: params }),
    onSuccess: (_, variables) => {
      switch (variables.type) {
        case "ssh-key":
          queryClient.invalidateQueries({ queryKey: ["org-ssh-keys"] })
          toast({
            title: "Created secret",
            description: "SSH key created successfully.",
          })
          break
        default:
          queryClient.invalidateQueries({ queryKey: ["org-custom-secrets"] })
          toast({
            title: "Created secret",
            description: "Secret created successfully.",
          })
          break
      }
    },
  })
  // update
  const { mutateAsync: updateSecretById } = useMutation({
    mutationFn: async ({
      secretId,
      params,
    }: {
      secretId: string
      params: SecretUpdate
    }) => await secretsUpdateSecretById({ secretId, requestBody: params }),
    onSuccess: (_, variables) => {
      switch (variables.params.type) {
        case "ssh-key":
          queryClient.invalidateQueries({ queryKey: ["org-ssh-keys"] })
          toast({
            title: "Updated secret",
            description: "SSH key updated successfully.",
          })
          break
        default:
          queryClient.invalidateQueries({ queryKey: ["org-custom-secrets"] })
          toast({
            title: "Updated secret",
            description: "Secret updated successfully.",
          })
          break
      }
    },
  })
  // delete
  const { mutateAsync: deleteSecretById } = useMutation({
    mutationFn: async (secret: SecretReadMinimal) =>
      await secretsDeleteSecretById({ secretId: secret.id }),
    onSuccess: (_, variables) => {
      switch (variables.type) {
        case "ssh-key":
          queryClient.invalidateQueries({ queryKey: ["org-ssh-keys"] })
          toast({
            title: "Deleted secret",
            description: "SSH key deleted successfully.",
          })
          break
        default:
          queryClient.invalidateQueries({ queryKey: ["org-custom-secrets"] })
          toast({
            title: "Deleted secret",
            description: "Secret deleted successfully.",
          })
          break
      }
    },
  })

  return {
    orgSecrets,
    orgSecretsIsLoading,
    orgSecretsError,
    createSecret,
    updateSecretById,
    deleteSecretById,
    orgSSHKeys,
    orgSSHKeysIsLoading,
    orgSSHKeysError,
  }
}

export function useUserManager() {
  const queryClient = useQueryClient()
  const {
    isPending: updateCurrentUserPending,
    mutateAsync: updateCurrentUser,
  } = useMutation({
    mutationFn: async (params: UserUpdate) =>
      await usersUsersPatchCurrentUser({
        requestBody: params,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["user"] })
      queryClient.invalidateQueries({ queryKey: ["auth"] })
      toast({
        title: "Updated user",
        description: "User updated successfully.",
      })
    },
    onError: (error: ApiError) => {
      switch (error.status) {
        case 400:
          console.error("User with email already exists", error)
          toast({
            title: "User with email already exists",
            description: "User with this email already exists.",
          })
          break
        default:
          console.error("Failed to update user", error)
          toast({
            title: "Failed to update user",
            description: "An error occurred while updating the user.",
          })
      }
    },
  })
  return {
    updateCurrentUser,
    updateCurrentUserPending,
  }
}

/* Registry Actions */
// For selector node
export function useWorkbenchRegistryActions(versions?: string[]) {
  const {
    data: registryActions,
    isLoading: registryActionsIsLoading,
    error: registryActionsError,
  } = useQuery<RegistryActionRead[]>({
    queryKey: ["workbench_registry_actions", versions],
    queryFn: async () => {
      return await registryActionsListRegistryActions()
    },
  })

  const getRegistryAction = (key: string): RegistryActionRead | undefined => {
    return registryActions?.find((action) => action.action === key)
  }

  return {
    registryActions,
    registryActionsIsLoading,
    registryActionsError,
    getRegistryAction,
  }
}

// This is for the action panel in the workbench
export function useRegistryAction(key: string, version: string) {
  const {
    data: registryAction,
    isLoading: registryActionIsLoading,
    error: registryActionError,
  } = useQuery<RegistryActionRead>({
    queryKey: ["registry_action", key, version],
    queryFn: async ({ queryKey }) => {
      return await registryActionsGetRegistryAction({
        actionName: queryKey[1] as string,
      })
    },
  })

  return { registryAction, registryActionIsLoading, registryActionError }
}

// For selector node
export function useRegistryActions(versions?: string[]) {
  const queryClient = useQueryClient()
  const {
    data: registryActions,
    isLoading: registryActionsIsLoading,
    error: registryActionsError,
  } = useQuery<RegistryActionRead[]>({
    queryKey: ["registry_actions", versions],
    queryFn: async () => {
      return await registryActionsListRegistryActions()
    },
  })

  const getRegistryAction = (
    actionName: string
  ): RegistryActionRead | undefined => {
    return registryActions?.find((action) => action.action === actionName)
  }

  const {
    mutateAsync: createRegistryAction,
    isPending: createRegistryActionIsPending,
    error: createRegistryActionError,
  } = useMutation({
    mutationFn: async (params: RegistryActionCreate) =>
      await registryActionsCreateRegistryAction({
        requestBody: params,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["registry_actions"] })
      toast({
        title: "Created registry action",
        description: "Registry action created successfully.",
      })
    },
    onError: (error: TracecatApiError) => {
      switch (error.status) {
        case 422:
          console.error("Failed to create registry action", error)
          toast({
            title: "Failed to create registry action",
            description:
              "An error occurred while creating the registry action.",
          })
          break
        default:
          console.error("Failed to create registry action", error)
          toast({
            title: "Failed to create registry action",
            description:
              "An error occurred while creating the registry action.",
          })
      }
    },
  })

  const {
    mutateAsync: updateRegistryAction,
    isPending: updateRegistryActionIsPending,
    error: updateRegistryActionError,
  } = useMutation({
    mutationFn: async (params: RegistryActionsUpdateRegistryActionData) =>
      await registryActionsUpdateRegistryAction(params),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["registry_actions"] })
      toast({
        title: "Updated registry action",
        description: "Registry action updated successfully.",
      })
    },
    onError: (error: TracecatApiError) => {
      console.error("Failed to update registry action", error)
      toast({
        title: "Failed to update registry action",
        description: "An error occurred while updating the registry action.",
      })
    },
  })

  const {
    mutateAsync: deleteRegistryAction,
    isPending: deleteRegistryActionIsPending,
    error: deleteRegistryActionError,
  } = useMutation({
    mutationFn: async (params: RegistryActionsDeleteRegistryActionData) =>
      await registryActionsDeleteRegistryAction(params),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["registry_actions"] })
      toast({
        title: "Deleted registry action",
        description: "Registry action deleted successfully.",
      })
    },
    onError: (error: TracecatApiError) => {
      console.error("Failed to delete registry action", error)
      const apiError = error as TracecatApiError
      switch (apiError.status) {
        case 400:
          toast({
            title: "Failed to delete registry action",
            description: apiError.message,
            variant: "destructive",
          })
          break
        case 403:
          toast({
            title: "Failed to delete registry action",
            description: `${apiError.message}: ${apiError.body.detail}`,
          })
          break
        case 404:
          toast({
            title: "Registry action not found",
            description: `${apiError.message}: ${apiError.body.detail}`,
            variant: "destructive",
          })
          break
        default:
          toast({
            title: "Failed to delete registry action",
            description:
              "An unexpected error occurred while deleting the registry action.",
            variant: "destructive",
          })
      }
    },
  })
  return {
    registryActions,
    registryActionsIsLoading,
    registryActionsError,
    getRegistryAction,
    createRegistryAction,
    createRegistryActionIsPending,
    createRegistryActionError,
    updateRegistryAction,
    updateRegistryActionIsPending,
    updateRegistryActionError,
    deleteRegistryAction,
    deleteRegistryActionIsPending,
    deleteRegistryActionError,
  }
}

export function useRegistryRepositories() {
  const queryClient = useQueryClient()
  const {
    data: registryRepos,
    isLoading: registryReposIsLoading,
    error: registryReposError,
  } = useQuery<RegistryRepositoryReadMinimal[]>({
    queryKey: ["registry_repositories"],
    queryFn: async () => await registryRepositoriesListRegistryRepositories(),
  })

  const {
    mutateAsync: syncRepos,
    isPending: syncReposIsPending,
    error: syncReposError,
  } = useMutation({
    mutationFn: async (
      params: RegistryRepositoriesSyncRegistryRepositoriesData
    ) => await registryRepositoriesSyncRegistryRepositories(params),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["registry_repositories", "registry_actions"],
      })
      toast({
        title: "Synced registry repositories",
        description: "Registry repositories synced successfully.",
      })
    },
    onError: (
      error: TracecatApiError,
      variables: RegistryRepositoriesSyncRegistryRepositoriesData
    ) => {
      const apiError = error as TracecatApiError
      switch (apiError.status) {
        case 400:
          toast({
            title: "Couldn't sync repositories",
            description: (
              <div>
                <p>Repositories: {variables.origins?.join(", ")}</p>
                <p>
                  {apiError.message}: {String(apiError.body.detail)}
                </p>
              </div>
            ),
          })
          break
        default:
          toast({
            title: "Unexpected error syncing repositories",
            description: (
              <div>
                <p>Repositories: {variables.origins?.join(", ")}</p>
                <p>{apiError.message}</p>
                <p>{apiError.body.detail as string}</p>
              </div>
            ),
            variant: "destructive",
          })
          break
      }
    },
  })

  const {
    mutateAsync: deleteRepo,
    isPending: deleteRepoIsPending,
    error: deleteRepoError,
  } = useMutation({
    mutationFn: async (
      params: RegistryRepositoriesDeleteRegistryRepositoryData
    ) => await registryRepositoriesDeleteRegistryRepository(params),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["registry_repositories"] })
      toast({
        title: "Deleted registry repository",
        description: "Registry repository deleted successfully.",
      })
    },
    onError: (error: TracecatApiError) => {
      const apiError = error as TracecatApiError
      switch (apiError.status) {
        case 403:
          toast({
            title: "You cannot perform this action",
            description: `${apiError.message}: ${apiError.body.detail}`,
          })
          break
        default:
          toast({
            title: "Failed to delete registry repository",
            description: `An unexpected error occurred while deleting the registry repository. ${apiError.message}: ${apiError.body.detail}`,
          })
      }
    },
  })

  return {
    registryRepos,
    registryReposIsLoading,
    registryReposError,
    syncRepos,
    syncReposIsPending,
    syncReposError,
    deleteRepo,
    deleteRepoIsPending,
    deleteRepoError,
  }
}

export function useOrgMembers() {
  const queryClient = useQueryClient()
  const { data: orgMembers } = useQuery<OrgMemberRead[]>({
    queryKey: ["org-members"],
    queryFn: async () => await organizationListOrgMembers(),
  })

  const {
    mutateAsync: updateOrgMember,
    isPending: updateOrgMemberIsPending,
    error: updateOrgMemberError,
  } = useMutation({
    mutationFn: async (params: OrganizationUpdateOrgMemberData) =>
      await organizationUpdateOrgMember(params),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["org-members"] })
      toast({
        title: "Updated organization member",
        description: "Organization member updated successfully.",
      })
    },
    onError: (error: TracecatApiError) => {
      const apiError = error as TracecatApiError
      switch (apiError.status) {
        case 403:
          toast({
            title: "You cannot perform this action",
            description: `${apiError.message}: ${apiError.body.detail}`,
          })
          break
        default:
          toast({
            title: "Failed to update organization member",
            description: `An unexpected error occurred while updating the organization member. ${apiError.message}: ${apiError.body.detail}`,
          })
      }
    },
  })

  const {
    mutateAsync: deleteOrgMember,
    isPending: deleteOrgMemberIsPending,
    error: deleteOrgMemberError,
  } = useMutation({
    mutationFn: async (params: OrganizationDeleteOrgMemberData) =>
      await organizationDeleteOrgMember(params),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["org-members"] })
      toast({
        title: "Deleted organization member",
        description: "Organization member deleted successfully.",
      })
    },
    onError: (error: TracecatApiError) => {
      const apiError = error as TracecatApiError
      switch (apiError.status) {
        case 403:
          toast({
            title: "You cannot perform this action",
            description: `${apiError.message}: ${apiError.body.detail}`,
          })
          break
        default:
          toast({
            title: "Failed to delete organization member",
            description: `An unexpected error occurred while deleting the organization member. ${apiError.message}: ${apiError.body.detail}`,
          })
      }
    },
  })

  return {
    orgMembers,
    updateOrgMember,
    updateOrgMemberIsPending,
    updateOrgMemberError,
    deleteOrgMember,
    deleteOrgMemberIsPending,
    deleteOrgMemberError,
  }
}
