import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import {
  ActionRead,
  actionsDeleteAction,
  ActionsDeleteActionData,
  actionsGetAction,
  actionsUpdateAction,
  ActionUpdate,
  ApiError,
  AppSettingsRead,
  AuthSettingsRead,
  CaseCommentCreate,
  CaseCommentRead,
  CaseCommentUpdate,
  CaseFieldRead,
  CaseRead,
  CaseReadMinimal,
  casesCreateComment,
  casesDeleteCase,
  casesDeleteComment,
  casesGetCase,
  CasesGetCaseData,
  casesListCases,
  CasesListCasesData,
  casesListComments,
  CasesListCommentsData,
  casesListFields,
  casesUpdateCase,
  casesUpdateComment,
  CaseUpdate,
  FolderDirectoryItem,
  foldersCreateFolder,
  foldersDeleteFolder,
  foldersGetDirectory,
  foldersListFolders,
  foldersMoveFolder,
  foldersUpdateFolder,
  GitSettingsRead,
  OAuthSettingsRead,
  organizationDeleteOrgMember,
  OrganizationDeleteOrgMemberData,
  organizationDeleteSession,
  OrganizationDeleteSessionData,
  organizationListOrgMembers,
  organizationListSessions,
  organizationSecretsCreateOrgSecret,
  organizationSecretsDeleteOrgSecretById,
  organizationSecretsListOrgSecrets,
  organizationSecretsUpdateOrgSecretById,
  organizationUpdateOrgMember,
  OrganizationUpdateOrgMemberData,
  OrgMemberRead,
  RegistryActionCreate,
  RegistryActionRead,
  RegistryActionReadMinimal,
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
  registryRepositoriesReloadRegistryRepositories,
  registryRepositoriesSyncRegistryRepository,
  RegistryRepositoriesSyncRegistryRepositoryData,
  RegistryRepositoryErrorDetail,
  RegistryRepositoryReadMinimal,
  SAMLSettingsRead,
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
  SessionRead,
  settingsGetAppSettings,
  settingsGetAuthSettings,
  settingsGetGitSettings,
  settingsGetOauthSettings,
  settingsGetSamlSettings,
  settingsUpdateAppSettings,
  SettingsUpdateAppSettingsData,
  settingsUpdateAuthSettings,
  SettingsUpdateAuthSettingsData,
  settingsUpdateGitSettings,
  SettingsUpdateGitSettingsData,
  settingsUpdateOauthSettings,
  SettingsUpdateOauthSettingsData,
  settingsUpdateSamlSettings,
  SettingsUpdateSamlSettingsData,
  TableRead,
  TableReadMinimal,
  TableRowRead,
  tablesBatchInsertRows,
  TablesBatchInsertRowsData,
  tablesCreateColumn,
  TablesCreateColumnData,
  tablesCreateTable,
  TablesCreateTableData,
  TablesCreateTableResponse,
  tablesDeleteColumn,
  TablesDeleteColumnData,
  tablesDeleteRow,
  TablesDeleteRowData,
  tablesDeleteTable,
  TablesDeleteTableData,
  tablesGetTable,
  TablesGetTableData,
  tablesImportCsv,
  TablesImportCsvData,
  tablesInsertRow,
  TablesInsertRowData,
  tablesListRows,
  TablesListRowsData,
  tablesListTables,
  TablesListTablesData,
  tablesUpdateColumn,
  TablesUpdateColumnData,
  tablesUpdateTable,
  TablesUpdateTableData,
  TagRead,
  tagsCreateTag,
  TagsCreateTagData,
  tagsDeleteTag,
  TagsDeleteTagData,
  tagsListTags,
  tagsUpdateTag,
  TagsUpdateTagData,
  triggersUpdateWebhook,
  usersUsersPatchCurrentUser,
  UserUpdate,
  WebhookUpdate,
  WorkflowDirectoryItem,
  WorkflowExecutionCreate,
  WorkflowExecutionRead,
  WorkflowExecutionReadCompact,
  WorkflowExecutionReadMinimal,
  workflowExecutionsCreateWorkflowExecution,
  workflowExecutionsGetWorkflowExecution,
  workflowExecutionsGetWorkflowExecutionCompact,
  workflowExecutionsListWorkflowExecutions,
  WorkflowFolderCreate,
  WorkflowFolderRead,
  WorkflowReadMinimal,
  workflowsAddTag,
  WorkflowsAddTagData,
  workflowsCreateWorkflow,
  WorkflowsCreateWorkflowData,
  workflowsDeleteWorkflow,
  workflowsListWorkflows,
  workflowsMoveWorkflowToFolder,
  WorkflowsMoveWorkflowToFolderData,
  workflowsRemoveTag,
  WorkflowsRemoveTagData,
  WorkspaceCreate,
  workspacesCreateWorkspace,
  workspacesDeleteWorkspace,
  workspacesListWorkspaces,
} from "@/client"
import { useWorkspace } from "@/providers/workspace"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import Cookies from "js-cookie"
import { AlertTriangleIcon, CircleCheck } from "lucide-react"

import { getBaseUrl } from "@/lib/api"
import { retryHandler, TracecatApiError } from "@/lib/errors"
import { toast } from "@/components/ui/use-toast"

interface AppInfo {
  version: string
  public_app_url: string
  auth_allowed_types: string[]
  auth_basic_enabled: boolean
  oauth_google_enabled: boolean
  saml_enabled: boolean
}

export function useAppInfo() {
  const {
    data: appInfo,
    isLoading: appInfoIsLoading,
    error: appInfoError,
  } = useQuery<AppInfo, Error>({
    queryKey: ["app-info"],
    queryFn: async () => {
      const resp = await fetch(getBaseUrl() + "/info")
      try {
        return await resp.json()
      } catch (error) {
        throw new Error(
          "Unable to fetch authentication settings. This could be a network issue with the Tracecat API."
        )
      }
    },
  })
  return { appInfo, appInfoIsLoading, appInfoError }
}

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
  workflowId: string | null
) {
  const [isSaving, setIsSaving] = useState(false)
  const queryClient = useQueryClient()
  const {
    data: action,
    isLoading: actionIsLoading,
    error: actionError,
  } = useQuery<ActionRead, Error>({
    enabled: !!workflowId,
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

export function useDeleteAction() {
  const queryClient = useQueryClient()
  const { mutateAsync: deleteAction } = useMutation({
    mutationFn: async (params: ActionsDeleteActionData) =>
      await actionsDeleteAction(params),
    onSuccess: (_, variables) => {
      const { actionId, workspaceId } = variables
      queryClient.invalidateQueries({
        queryKey: ["actions", actionId, workspaceId],
      })
    },
    onError: (error) => {
      console.error("Failed to delete action:", error)
    },
  })
  return { deleteAction }
}

export function useUpdateWebhook(workspaceId: string, workflowId: string) {
  const queryClient = useQueryClient()
  const mutation = useMutation({
    mutationFn: async (params: WebhookUpdate) =>
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

interface WorkflowFilter {
  tag?: string[]
  folderId?: string
}

export function useWorkflowManager(filter?: WorkflowFilter) {
  const queryClient = useQueryClient()
  const { workspaceId } = useWorkspace()

  // List workflows
  const {
    data: workflows,
    isLoading: workflowsLoading,
    error: workflowsError,
  } = useQuery<WorkflowReadMinimal[], ApiError>({
    queryKey: ["workflows", filter?.tag],
    queryFn: async () =>
      await workflowsListWorkflows({ workspaceId, tag: filter?.tag }),
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
      queryClient.invalidateQueries({ queryKey: ["directory-items"] })
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
  // Add tag to workflow
  const { mutateAsync: addWorkflowTag } = useMutation({
    mutationFn: async (params: WorkflowsAddTagData) =>
      await workflowsAddTag(params),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["workflows"] })
    },
    onError: (error: TracecatApiError) => {
      console.error("Failed to add tag to workflow:", error)
      toast({
        title: "Couldn't add tag to workflow",
        description: error.body.detail + ". Please try again.",
      })
    },
  })
  // Remove tag from workflow
  const { mutateAsync: removeWorkflowTag } = useMutation({
    mutationFn: async (params: WorkflowsRemoveTagData) =>
      await workflowsRemoveTag(params),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["workflows"] })
    },
    onError: (error: TracecatApiError) => {
      console.error("Failed to remove tag from workflow:", error)
      toast({
        title: "Couldn't remove tag from workflow",
        description: error.body.detail + ". Please try again.",
      })
    },
  })

  // Move workflow
  const { mutateAsync: moveWorkflow } = useMutation({
    mutationFn: async (params: WorkflowsMoveWorkflowToFolderData) =>
      await workflowsMoveWorkflowToFolder(params),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["directory-items"] })
    },
    onError: (error: TracecatApiError) => {
      console.error("Failed to move workflow:", error)
      toast({
        title: "Error moving workflow",
        description: error.body.detail + ". Please try again.",
      })
    },
  })

  return {
    workflows,
    workflowsLoading,
    workflowsError,
    createWorkflow,
    deleteWorkflow,
    addWorkflowTag,
    removeWorkflowTag,
    moveWorkflow,
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
    mutationFn: async (params: WorkspaceCreate) =>
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
    /**
     * Refetch interval in milliseconds
     */
    refetchInterval?: number
  }
) {
  const { workspaceId } = useWorkspace()
  const {
    data: workflowExecutions,
    isLoading: workflowExecutionsIsLoading,
    error: workflowExecutionsError,
  } = useQuery<WorkflowExecutionReadMinimal[], Error>({
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

export function useWorkflowExecution(
  executionId: string,
  options?: {
    refetchInterval?: number
  }
) {
  const { workspaceId } = useWorkspace()
  const {
    data: execution,
    isLoading: executionIsLoading,
    error: executionError,
  } = useQuery<WorkflowExecutionRead, ApiError>({
    queryKey: ["workflow-executions", executionId],
    queryFn: async () =>
      await workflowExecutionsGetWorkflowExecution({
        workspaceId,
        executionId: executionId,
      }),
    retry: retryHandler,
    ...options,
  })
  return {
    execution,
    executionIsLoading,
    executionError,
  }
}

export function useCompactWorkflowExecution(workflowExecutionId?: string) {
  // if execution ID contains non-url-safe characters, decode it
  const { workspaceId } = useWorkspace()
  const {
    data: execution,
    isLoading: executionIsLoading,
    error: executionError,
  } = useQuery<WorkflowExecutionReadCompact | null, ApiError>({
    enabled: !!workflowExecutionId,
    queryKey: ["compact-workflow-execution", workflowExecutionId],
    queryFn: async () => {
      if (!workflowExecutionId) return null
      return await workflowExecutionsGetWorkflowExecutionCompact({
        workspaceId,
        executionId: encodeURIComponent(workflowExecutionId),
      })
    },
    // Add retry logic for potential 404s when the execution hasn't been fully registered
    retry: (failureCount, error) => error?.status === 404 && failureCount < 10,
    retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 4000),
    // Use more dynamic polling interval based on execution status
    refetchInterval: (query) => {
      // If we don't have data yet, poll more frequently
      // if (!query.state.data) {
      //   return 1000
      // }

      // Adjust polling based on workflow status
      switch (query.state.data?.status) {
        case "RUNNING":
          console.log("Running, polling every 1000ms")
          return 1000
        default:
          return false
      }
    },
    // Don't cache stale data in this context
    // staleTime: 0,
  })

  return {
    execution,
    executionIsLoading,
    executionError,
  }
}

export function useCreateManualWorkflowExecution(workflowId: string) {
  const queryClient = useQueryClient()
  const { workspaceId } = useWorkspace()

  const {
    mutateAsync: createExecution,
    isPending: createExecutionIsPending,
    error: createExecutionError,
  } = useMutation({
    mutationFn: async (params: WorkflowExecutionCreate) => {
      return await workflowExecutionsCreateWorkflowExecution({
        workspaceId,
        requestBody: params,
      })
    },
    onSuccess: async ({ wf_exec_id, message }) => {
      toast({
        title: `Workflow run started`,
        description: `${wf_exec_id} ${message}`,
      })

      // Still invalidate queries for compatibility with other components
      await queryClient.refetchQueries({
        queryKey: ["last-manual-execution"],
      })
      await queryClient.refetchQueries({
        queryKey: ["last-manual-execution", workflowId],
      })
      await queryClient.refetchQueries({
        queryKey: ["compact-workflow-execution"],
      })
      await queryClient.refetchQueries({
        queryKey: ["compact-workflow-execution", wf_exec_id],
      })
    },
    onError: (error: TracecatApiError<Record<string, string>>) => {
      switch (error.status) {
        case 400:
          console.error("Invalid workflow trigger inputs", error)
          return toast({
            title: "Invalid workflow trigger inputs",
            description: "Please hover over the run button for details.",
          })
        default:
          console.error("Unexpected error starting workflow", error)
          return toast({
            title: "Unexpected error starting workflow",
            description: "Please check the run logs for more information",
          })
      }
    },
  })

  return {
    createExecution,
    createExecutionIsPending,
    createExecutionError,
  }
}

export function useLastManualExecution(workflowId?: string) {
  const { workspaceId } = useWorkspace()
  const {
    data: lastExecution,
    isLoading: lastExecutionIsLoading,
    error: lastExecutionError,
  } = useQuery<WorkflowExecutionReadMinimal | null, TracecatApiError>({
    enabled: !!workflowId,
    queryKey: ["last-manual-execution", workflowId],
    queryFn: async () => {
      const executions = await workflowExecutionsListWorkflowExecutions({
        workspaceId,
        workflowId,
        trigger: ["manual"],
        limit: 1,
        userId: "current",
      })

      return executions.length > 0 ? executions[0] : null
    },
  })

  return {
    lastExecution,
    lastExecutionIsLoading,
    lastExecutionError,
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
        case 403:
          return toast({
            title: "Forbidden",
            description: "You cannot create secrets in this workspace.",
          })
        case 409:
          return toast({
            title: "Secret already exists",
            description:
              "Secrets with the same name and environment are not supported.",
          })
        default:
          console.error("Failed to create secret", error)
          return toast({
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
    onError: (error: TracecatApiError) => {
      switch (error.status) {
        case 403:
          return toast({
            title: "Forbidden",
            description: "You cannot update secrets in this workspace.",
          })
        default:
          console.error("Failed to update secret", error)
          return toast({
            title: "Failed to update secret",
            description: "An error occurred while updating the secret.",
          })
      }
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
    onError: (error: TracecatApiError) => {
      switch (error.status) {
        case 403:
          return toast({
            title: "Forbidden",
            description: "You cannot delete secrets in this workspace.",
          })
        default:
          console.error("Failed to delete secret", error)
          return toast({
            title: "Failed to delete secret",
            description: "An error occurred while deleting the secret.",
          })
      }
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
      await organizationSecretsListOrgSecrets({
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
      await organizationSecretsListOrgSecrets({
        type: ["ssh-key"],
      }),
  })

  // create
  const { mutateAsync: createSecret } = useMutation({
    mutationFn: async (params: SecretCreate) =>
      await organizationSecretsCreateOrgSecret({ requestBody: params }),
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
    }) =>
      await organizationSecretsUpdateOrgSecretById({
        secretId,
        requestBody: params,
      }),
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
      await organizationSecretsDeleteOrgSecretById({ secretId: secret.id }),
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
  } = useQuery<RegistryActionReadMinimal[]>({
    queryKey: ["workbench_registry_actions", versions],
    queryFn: async () => {
      return await registryActionsListRegistryActions()
    },
  })

  const getRegistryAction = (
    key: string
  ): RegistryActionReadMinimal | undefined => {
    return registryActions?.find((action) => action.action === key)
  }

  return {
    registryActions,
    registryActionsIsLoading,
    registryActionsError,
    getRegistryAction,
  }
}

export function useGetRegistryAction(actionName?: string) {
  const {
    data: registryAction,
    isLoading: registryActionIsLoading,
    error: registryActionError,
  } = useQuery<RegistryActionRead | undefined, ApiError>({
    queryKey: ["registry_action", actionName],
    queryFn: async () => {
      if (!actionName) {
        return undefined
      }
      return await registryActionsGetRegistryAction({
        actionName,
      })
    },
    retry: retryHandler,
    enabled: !!actionName,
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
  } = useQuery<RegistryActionReadMinimal[]>({
    queryKey: ["registry_actions", versions],
    queryFn: async () => {
      return await registryActionsListRegistryActions()
    },
  })

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
    data: repos,
    isLoading: reposIsLoading,
    error: reposError,
  } = useQuery<RegistryRepositoryReadMinimal[]>({
    queryKey: ["registry_repositories"],
    queryFn: async () => await registryRepositoriesListRegistryRepositories(),
  })

  const {
    mutateAsync: syncRepo,
    isPending: syncRepoIsPending,
    error: syncRepoError,
  } = useMutation({
    mutationFn: async (
      params: RegistryRepositoriesSyncRegistryRepositoryData
    ) => await registryRepositoriesSyncRegistryRepository(params),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["registry_repositories"],
      })
      queryClient.invalidateQueries({
        queryKey: ["registry_actions"],
      })
      toast({
        title: "Synced registry repositories",
        description: "Registry repositories synced successfully.",
      })
    },
    onError: (error: TracecatApiError) => {
      switch (error.status) {
        case 400:
          return toast({
            title: "Couldn't sync repository",
            description: (
              <div className="flex items-start gap-2">
                <AlertTriangleIcon className="size-4 fill-rose-600 stroke-white" />
                <span>{String(error.body.detail)}</span>
              </div>
            ),
          })
        case 403:
          return toast({
            title: "Forbidden",
            description: "You are not authorized to perform this action",
          })
        case 422:
          const { message } = error.body.detail as RegistryRepositoryErrorDetail
          return toast({
            title: "Repository validation failed",
            description: (
              <div className="flex items-start gap-2">
                <AlertTriangleIcon className="size-4 fill-rose-600 stroke-white" />
                <span>{message}</span>
              </div>
            ),
          })
        default:
          return toast({
            title: "Unexpected error syncing repositories",
            description: (
              <div className="flex items-start gap-2">
                <AlertTriangleIcon className="size-4 fill-rose-600 stroke-white" />
                <span>{error.message}</span>
                <span>{String(error.body.detail)}</span>
              </div>
            ),
          })
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
    repos,
    reposIsLoading,
    reposError,
    syncRepo,
    syncRepoIsPending,
    syncRepoError,
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

export function useSessions() {
  const queryClient = useQueryClient()
  // List
  const {
    data: sessions,
    isLoading: sessionsIsLoading,
    error: sessionsError,
  } = useQuery<SessionRead[]>({
    queryKey: ["sessions"],
    queryFn: async () => await organizationListSessions(),
  })

  // Delete
  const {
    mutateAsync: deleteSession,
    isPending: deleteSessionIsPending,
    error: deleteSessionError,
  } = useMutation({
    mutationFn: async (params: OrganizationDeleteSessionData) =>
      await organizationDeleteSession(params),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["sessions"] })
      toast({
        title: "Revoked session",
        description: "Session revoked successfully.",
      })
    },
  })

  return {
    sessions,
    sessionsIsLoading,
    sessionsError,
    deleteSession,
    deleteSessionIsPending,
    deleteSessionError,
  }
}

export function useTags(workspaceId: string) {
  const queryClient = useQueryClient()

  // List tags
  const {
    data: tags,
    isLoading: tagsIsLoading,
    error: tagsError,
  } = useQuery<TagRead[]>({
    queryKey: ["tags", workspaceId],
    queryFn: async () => await tagsListTags({ workspaceId }),
  })

  // Create tag
  const {
    mutateAsync: createTag,
    isPending: createTagIsPending,
    error: createTagError,
  } = useMutation({
    mutationFn: async (params: TagsCreateTagData) =>
      await tagsCreateTag(params),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tags", workspaceId] })
      toast({
        title: "Created tag",
        description: (
          <div className="flex items-center space-x-2">
            <CircleCheck className="size-4 fill-emerald-500 stroke-white" />
            <span>Tag created successfully.</span>
          </div>
        ),
      })
    },

    onError: (error: TracecatApiError) => {
      switch (error.status) {
        case 400:
          console.error("Error creating tag", error)
          toast({
            title: "Error creating tag",
            description: String(error.body.detail),
          })
          break
        case 403:
          toast({
            title: "Forbidden",
            description: "You cannot perform this action",
          })
          break
        default:
          console.error("Failed to create tag", error)
          toast({
            title: "Failed to create tag",
            description: `An error occurred while creating the tag: ${error.body.detail}`,
          })
      }
    },
  })

  // Update tag
  const {
    mutateAsync: updateTag,
    isPending: updateTagIsPending,
    error: updateTagError,
  } = useMutation({
    mutationFn: async (params: TagsUpdateTagData) =>
      await tagsUpdateTag(params),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tags", workspaceId] })
      queryClient.invalidateQueries({ queryKey: ["workflows"] })
      toast({
        title: "Updated tag",
        description: "Tag updated successfully.",
      })
    },
    onError: (error: TracecatApiError) => {
      switch (error.status) {
        case 400:
          console.error("Error updating tag", error)
          toast({
            title: "Error updating tag",
            description: String(error.body.detail),
          })
          break
      }
    },
  })

  // Delete tag
  const {
    mutateAsync: deleteTag,
    isPending: deleteTagIsPending,
    error: deleteTagError,
  } = useMutation({
    mutationFn: async (params: TagsDeleteTagData) =>
      await tagsDeleteTag(params),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tags", workspaceId] })
      queryClient.invalidateQueries({ queryKey: ["workflows"] })
      toast({
        title: "Deleted tag",
        description: "Tag deleted successfully.",
      })
    },
    onError: (error: TracecatApiError) => {
      switch (error.status) {
        case 403:
          toast({
            title: "Forbidden",
            description: "You cannot perform this action",
          })
          break
      }
    },
  })

  return {
    // List
    tags,
    tagsIsLoading,
    tagsError,
    // Create
    createTag,
    createTagIsPending,
    createTagError,
    // Update
    updateTag,
    updateTagIsPending,
    updateTagError,
    // Delete
    deleteTag,
    deleteTagIsPending,
    deleteTagError,
  }
}

export function useOrgGitSettings() {
  const queryClient = useQueryClient()
  // Get Git settings
  const {
    data: gitSettings,
    isLoading: gitSettingsIsLoading,
    error: gitSettingsError,
  } = useQuery<GitSettingsRead>({
    queryKey: ["org-git-settings"],
    queryFn: async () => await settingsGetGitSettings(),
  })

  // Update Git settings
  const {
    mutateAsync: updateGitSettings,
    isPending: updateGitSettingsIsPending,
    error: updateGitSettingsError,
  } = useMutation({
    mutationFn: async (params: SettingsUpdateGitSettingsData) =>
      await settingsUpdateGitSettings(params),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["org-git-settings"] })
      toast({
        title: "Updated Git settings",
        description: "Git settings updated successfully.",
      })
    },
    onError: (error: TracecatApiError) => {
      switch (error.status) {
        case 403:
          toast({
            title: "Forbidden",
            description: "You cannot perform this action",
          })
          break
        default:
          console.error("Failed to update Git settings", error)
          toast({
            title: "Failed to update Git settings",
            description: `An error occurred while updating the Git settings: ${error.body.detail}`,
          })
      }
    },
  })

  return {
    // Get
    gitSettings,
    gitSettingsIsLoading,
    gitSettingsError,
    // Update
    updateGitSettings,
    updateGitSettingsIsPending,
    updateGitSettingsError,
  }
}

export function useOrgSamlSettings() {
  const queryClient = useQueryClient()

  // Get SAML settings
  const {
    data: samlSettings,
    isLoading: samlSettingsIsLoading,
    error: samlSettingsError,
  } = useQuery<SAMLSettingsRead>({
    queryKey: ["org-saml-settings"],
    queryFn: async () => await settingsGetSamlSettings(),
  })

  // Update SAML settings
  const {
    mutateAsync: updateSamlSettings,
    isPending: updateSamlSettingsIsPending,
    error: updateSamlSettingsError,
  } = useMutation({
    mutationFn: async (params: SettingsUpdateSamlSettingsData) =>
      await settingsUpdateSamlSettings(params),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["org-saml-settings"] })
      toast({
        title: "Updated SAML settings",
        description: "SAML settings updated successfully.",
      })
    },
    onError: (error: TracecatApiError) => {
      switch (error.status) {
        case 403:
          toast({
            title: "Forbidden",
            description: "You cannot perform this action",
          })
          break
        default:
          console.error("Failed to update SAML settings", error)
          toast({
            title: "Failed to update SAML settings",
            description: `An error occurred while updating the SAML settings: ${error.body.detail}`,
          })
      }
    },
  })

  return {
    // Get
    samlSettings,
    samlSettingsIsLoading,
    samlSettingsError,
    // Update
    updateSamlSettings,
    updateSamlSettingsIsPending,
    updateSamlSettingsError,
  }
}

export function useOrgAppSettings() {
  const queryClient = useQueryClient()

  // Get App settings
  const {
    data: appSettings,
    isLoading: appSettingsIsLoading,
    error: appSettingsError,
  } = useQuery<AppSettingsRead>({
    queryKey: ["org-app-settings"],
    queryFn: async () => await settingsGetAppSettings(),
  })

  // Update App settings
  const {
    mutateAsync: updateAppSettings,
    isPending: updateAppSettingsIsPending,
    error: updateAppSettingsError,
  } = useMutation({
    mutationFn: async (params: SettingsUpdateAppSettingsData) =>
      await settingsUpdateAppSettings(params),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["org-app-settings"] })
      toast({
        title: "Updated App settings",
        description: "App settings updated successfully.",
      })
    },
    onError: (error: TracecatApiError) => {
      switch (error.status) {
        case 403:
          toast({
            title: "Forbidden",
            description: "You cannot perform this action",
          })
          break
        default:
          console.error("Failed to update App settings", error)
          toast({
            title: "Failed to update App settings",
            description: `An error occurred while updating the App settings: ${error.body.detail}`,
          })
      }
    },
  })

  return {
    // Get
    appSettings,
    appSettingsIsLoading,
    appSettingsError,
    // Update
    updateAppSettings,
    updateAppSettingsIsPending,
    updateAppSettingsError,
  }
}

export function useRegistryRepositoriesReload() {
  const queryClient = useQueryClient()
  const {
    mutateAsync: reloadRegistryRepositories,
    isPending: reloadRegistryRepositoriesIsPending,
    error: reloadRegistryRepositoriesError,
  } = useMutation({
    mutationFn: async () =>
      await registryRepositoriesReloadRegistryRepositories(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["registry_repositories"] })
      toast({
        title: "Reloaded repositories",
        description: "Repositories reloaded successfully.",
      })
    },
    onError: (error: TracecatApiError) => {
      switch (error.status) {
        case 403:
          return toast({
            title: "Forbidden",
            description: "You are not authorized to perform this action",
          })
        default:
          console.error("Failed to reload repositories", error)
          return toast({
            title: "Failed to reload repositories",
            description: `An error occurred while reloading the repositories: ${error.body.detail}`,
          })
      }
    },
  })

  return {
    reloadRegistryRepositories,
    reloadRegistryRepositoriesIsPending,
    reloadRegistryRepositoriesError,
  }
}

export function useOrgAuthSettings() {
  const queryClient = useQueryClient()

  // Get Auth settings
  const {
    data: authSettings,
    isLoading: authSettingsIsLoading,
    error: authSettingsError,
  } = useQuery<AuthSettingsRead>({
    queryKey: ["org-auth-settings"],
    queryFn: async () => await settingsGetAuthSettings(),
  })

  // Update Auth settings
  const {
    mutateAsync: updateAuthSettings,
    isPending: updateAuthSettingsIsPending,
    error: updateAuthSettingsError,
  } = useMutation({
    mutationFn: async (params: SettingsUpdateAuthSettingsData) =>
      await settingsUpdateAuthSettings(params),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["org-auth-settings"] })
      toast({
        title: "Updated Auth settings",
        description: "Auth settings updated successfully.",
      })
    },
    onError: (error: TracecatApiError) => {
      switch (error.status) {
        case 403:
          toast({
            title: "Forbidden",
            description: "You cannot perform this action",
          })
          break
        default:
          console.error("Failed to update auth settings", error)
          toast({
            title: "Failed to update auth settings",
            description: `An error occurred while updating the auth settings: ${error.body.detail}`,
          })
      }
    },
  })

  return {
    // Get
    authSettings,
    authSettingsIsLoading,
    authSettingsError,
    // Update
    updateAuthSettings,
    updateAuthSettingsIsPending,
    updateAuthSettingsError,
  }
}

export function useOrgOAuthSettings() {
  const queryClient = useQueryClient()

  // Get OAuth settings
  const {
    data: oauthSettings,
    isLoading: oauthSettingsIsLoading,
    error: oauthSettingsError,
  } = useQuery<OAuthSettingsRead>({
    queryKey: ["org-oauth-settings"],
    queryFn: async () => await settingsGetOauthSettings(),
  })

  // Update OAuth settings
  const {
    mutateAsync: updateOAuthSettings,
    isPending: updateOAuthSettingsIsPending,
    error: updateOAuthSettingsError,
  } = useMutation({
    mutationFn: async (params: SettingsUpdateOauthSettingsData) =>
      await settingsUpdateOauthSettings(params),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["org-oauth-settings"] })
      toast({
        title: "Updated OAuth settings",
        description: "OAuth settings updated successfully.",
      })
    },
    onError: (error: TracecatApiError) => {
      switch (error.status) {
        case 403:
          toast({
            title: "Forbidden",
            description: "You cannot perform this action",
          })
          break
        default:
          console.error("Failed to update OAuth settings", error)
          toast({
            title: "Failed to update OAuth settings",
            description: `An error occurred while updating the OAuth settings: ${error.body.detail}`,
          })
      }
    },
  })

  return {
    // Get
    oauthSettings,
    oauthSettingsIsLoading,
    oauthSettingsError,
    // Update
    updateOAuthSettings,
    updateOAuthSettingsIsPending,
    updateOAuthSettingsError,
  }
}

export function useListTables({ workspaceId }: TablesListTablesData) {
  const {
    data: tables,
    isLoading: tablesIsLoading,
    error: tablesError,
  } = useQuery<TableReadMinimal[], ApiError>({
    queryKey: ["tables", workspaceId],
    queryFn: async () => await tablesListTables({ workspaceId }),
  })

  return {
    tables,
    tablesIsLoading,
    tablesError,
  }
}

export function useGetTable({ tableId, workspaceId }: TablesGetTableData) {
  const {
    data: table,
    isLoading: tableIsLoading,
    error: tableError,
  } = useQuery<TableRead, ApiError>({
    queryKey: ["table", tableId],
    queryFn: async () => await tablesGetTable({ tableId, workspaceId }),
  })

  return {
    table,
    tableIsLoading,
    tableError,
  }
}

export function useCreateTable() {
  const queryClient = useQueryClient()
  const {
    mutateAsync: createTable,
    isPending: createTableIsPending,
    error: createTableError,
  } = useMutation<
    TablesCreateTableResponse,
    TracecatApiError,
    TablesCreateTableData
  >({
    mutationFn: async (params: TablesCreateTableData) =>
      await tablesCreateTable(params),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({
        queryKey: ["tables", variables.workspaceId],
      })
    },
    onError: (error: TracecatApiError) => {
      switch (error.status) {
        case 403:
          toast({
            title: "Forbidden",
            description: "You cannot perform this action",
          })
          break
        default:
          console.error("Error creating table", error)
          break
      }
    },
  })

  return {
    createTable,
    createTableIsPending,
    createTableError,
  }
}

export function useUpdateTable() {
  const queryClient = useQueryClient()
  const {
    mutateAsync: updateTable,
    isPending: updateTableIsPending,
    error: updateTableError,
  } = useMutation({
    mutationFn: async (params: TablesUpdateTableData) =>
      await tablesUpdateTable(params),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({
        queryKey: ["tables", variables.workspaceId],
      })
      queryClient.invalidateQueries({
        queryKey: ["table", variables.tableId],
      })
    },
  })

  return {
    updateTable,
    updateTableIsPending,
    updateTableError,
  }
}

export function useDeleteTable() {
  const queryClient = useQueryClient()
  const {
    mutateAsync: deleteTable,
    isPending: deleteTableIsPending,
    error: deleteTableError,
  } = useMutation({
    mutationFn: async (params: TablesDeleteTableData) =>
      await tablesDeleteTable(params),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({
        queryKey: ["tables", variables.workspaceId],
      })
      queryClient.invalidateQueries({
        queryKey: ["table", variables.tableId],
      })
    },
    onError: (error: TracecatApiError) => {
      switch (error.status) {
        case 403:
          return toast({
            title: "Forbidden",
            description: "You cannot perform this action",
          })
        case 400:
          return toast({
            title: "Bad Request",
            description: String(error.body.detail),
          })
        default:
          console.error("Error deleting table", error)
          return toast({
            title: "Error deleting table",
            description: `An error occurred while deleting the table: ${error.body.detail}`,
          })
      }
    },
  })

  return {
    deleteTable,
    deleteTableIsPending,
    deleteTableError,
  }
}

export function useListRows({ tableId, workspaceId }: TablesListRowsData) {
  const {
    data: rows,
    isLoading: rowsIsLoading,
    error: rowsError,
  } = useQuery<TableRowRead[], TracecatApiError>({
    queryKey: ["rows", tableId],
    queryFn: async () => await tablesListRows({ tableId, workspaceId }),
  })

  return {
    rows,
    rowsIsLoading,
    rowsError,
  }
}

export function useInsertColumn() {
  const queryClient = useQueryClient()
  const {
    mutateAsync: insertColumn,
    isPending: insertColumnIsPending,
    error: insertColumnError,
  } = useMutation({
    mutationFn: async (params: TablesCreateColumnData) =>
      await tablesCreateColumn(params),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({
        queryKey: ["tables", variables.workspaceId],
      })
      queryClient.invalidateQueries({
        queryKey: ["table", variables.tableId],
      })
      queryClient.invalidateQueries({
        queryKey: ["rows", variables.tableId],
      })
    },
    onError: (error: TracecatApiError) => {
      switch (error.status) {
        case 403:
          toast({
            title: "Forbidden",
            description: "You cannot perform this action",
          })
          break
        default:
          console.error("Error inserting column", error)
          break
      }
    },
  })

  return {
    insertColumn,
    insertColumnIsPending,
    insertColumnError,
  }
}

export function useUpdateColumn() {
  const queryClient = useQueryClient()
  const {
    mutateAsync: updateColumn,
    isPending: updateColumnIsPending,
    error: updateColumnError,
  } = useMutation({
    mutationFn: async (params: TablesUpdateColumnData) =>
      await tablesUpdateColumn(params),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({
        queryKey: ["rows", variables.tableId],
      })
      queryClient.invalidateQueries({
        queryKey: ["table", variables.tableId],
      })
    },
    onError: (error: TracecatApiError, variables) => {
      // Check if this was a unique index operation
      const isIndexOperation = !!variables.requestBody?.is_index

      if (isIndexOperation) {
        // Handle unique index specific errors
        if (error.status === 409) {
          toast({
            title: "Error creating unique index",
            description:
              "Column contains duplicate values. All values must be unique.",
          })
        } else if (error.status === 400) {
          toast({
            title: "Error creating unique index",
            description: String(error.body.detail),
          })
        } else {
          toast({
            title: "Error creating unique index",
            description: error.message || "An unexpected error occurred",
          })
        }
      } else {
        // Handle regular column update errors
        switch (error.status) {
          case 403:
            toast({
              title: "Forbidden",
              description: "You cannot perform this action",
            })
            break
          default:
            console.error("Error updating column", error)
            toast({
              title: "Error updating column",
              description: error.message || "An unexpected error occurred",
            })
            break
        }
      }
    },
  })

  return {
    updateColumn,
    updateColumnIsPending,
    updateColumnError,
  }
}

export function useDeleteColumn() {
  const queryClient = useQueryClient()
  const {
    mutateAsync: deleteColumn,
    isPending: deleteColumnIsPending,
    error: deleteColumnError,
  } = useMutation({
    mutationFn: async (params: TablesDeleteColumnData) =>
      await tablesDeleteColumn(params),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({
        queryKey: ["rows", variables.tableId],
      })
      queryClient.invalidateQueries({
        queryKey: ["table", variables.tableId],
      })
    },
  })

  return {
    deleteColumn,
    deleteColumnIsPending,
    deleteColumnError,
  }
}

export function useBatchInsertRows() {
  const queryClient = useQueryClient()
  const {
    mutateAsync: insertRows,
    isPending: insertRowsIsPending,
    error: insertRowsError,
  } = useMutation({
    mutationFn: async (params: TablesBatchInsertRowsData) =>
      await tablesBatchInsertRows(params),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({
        queryKey: ["rows", variables.tableId],
      })
      toast({
        title: "Imported rows successfully",
        description: "The data has been imported into the table.",
      })
    },
    onError: (error: TracecatApiError) => {
      console.error("Error batch inserting rows:", error)
      toast({
        title: "Failed to import rows",
        description: "There was an error importing the data. Please try again.",
        variant: "destructive",
      })
    },
  })

  return {
    insertRows,
    insertRowsIsPending,
    insertRowsError,
  }
}

export function useInsertRow() {
  const queryClient = useQueryClient()

  const {
    mutateAsync: insertRow,
    isPending: insertRowIsPending,
    error: insertRowError,
  } = useMutation({
    mutationFn: async (params: TablesInsertRowData) =>
      await tablesInsertRow(params),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({
        queryKey: ["rows", variables.tableId],
      })
    },
    onError: (error: TracecatApiError, variables) => {
      if (error.status === 409) {
        toast({
          title: "Duplicate Value Error",
          description:
            "Cannot insert duplicate values in a unique column. Please use unique values.",
          variant: "destructive",
        })
      } else {
        toast({
          title: "Error inserting row",
          description: error.message || "An unexpected error occurred",
          variant: "destructive",
        })
      }
    },
  })

  return {
    insertRow,
    insertRowIsPending,
    insertRowError,
  }
}

export function useDeleteRow() {
  const queryClient = useQueryClient()
  const {
    mutateAsync: deleteRow,
    isPending: deleteRowIsPending,
    error: deleteRowError,
  } = useMutation({
    mutationFn: async (params: TablesDeleteRowData) =>
      await tablesDeleteRow(params),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({
        queryKey: ["rows", variables.tableId],
      })
      queryClient.invalidateQueries({
        queryKey: ["table", variables.tableId],
      })
    },
  })

  return {
    deleteRow,
    deleteRowIsPending,
    deleteRowError,
  }
}

export function useImportCsv() {
  const queryClient = useQueryClient()
  const {
    mutateAsync: importCsv,
    isPending: importCsvIsPending,
    error: importCsvError,
  } = useMutation({
    mutationFn: async (params: TablesImportCsvData) =>
      await tablesImportCsv(params),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({
        queryKey: ["rows", variables.tableId],
      })
      toast({
        title: "Imported rows successfully",
        description: "The data has been imported into the table.",
      })
    },
  })

  return {
    importCsv,
    importCsvIsPending,
    importCsvError,
  }
}

export function useListCases({ workspaceId }: CasesListCasesData) {
  const {
    data: cases,
    isLoading: casesIsLoading,
    error: casesError,
  } = useQuery<CaseReadMinimal[], TracecatApiError>({
    queryKey: ["cases", workspaceId],
    queryFn: async () => await casesListCases({ workspaceId }),
  })

  return {
    cases,
    casesIsLoading,
    casesError,
  }
}

export function useGetCase({ caseId, workspaceId }: CasesGetCaseData) {
  const {
    data: caseData,
    isLoading: caseDataIsLoading,
    error: caseDataError,
  } = useQuery<CaseRead, TracecatApiError>({
    queryKey: ["case", caseId],
    queryFn: async () => await casesGetCase({ caseId, workspaceId }),
  })

  return {
    caseData,
    caseDataIsLoading,
    caseDataError,
  }
}

export function useUpdateCase({
  workspaceId,
  caseId,
}: {
  workspaceId: string
  caseId: string
}) {
  const queryClient = useQueryClient()
  const {
    mutateAsync: updateCase,
    isPending: updateCaseIsPending,
    error: updateCaseError,
  } = useMutation({
    mutationFn: async (params: CaseUpdate) =>
      await casesUpdateCase({ caseId, workspaceId, requestBody: params }),

    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["cases", workspaceId],
      })
      queryClient.invalidateQueries({
        queryKey: ["case", caseId],
      })
    },
    onError: (error: TracecatApiError) => {
      switch (error.status) {
        case 403:
          return toast({
            title: "Forbidden",
            description: "You cannot perform this action",
          })
        default:
          console.error("Error updating case", error)
          return toast({
            title: "Error updating case",
            description: `An error occurred while updating the case: ${error.body.detail}`,
            variant: "destructive",
          })
      }
    },
  })

  return {
    updateCase,
    updateCaseIsPending,
    updateCaseError,
  }
}

export function useDeleteCase({ workspaceId }: { workspaceId: string }) {
  const queryClient = useQueryClient()

  const {
    mutateAsync: deleteCase,
    isPending: deleteCaseIsPending,
    error: deleteCaseError,
  } = useMutation({
    mutationFn: async (caseId: string) =>
      await casesDeleteCase({ workspaceId, caseId }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["cases", workspaceId],
      })
    },
    onError: (error: TracecatApiError) => {
      switch (error.status) {
        case 403:
          return toast({
            title: "Forbidden",
            description: "You cannot perform this action",
          })
        default:
          console.error("Error deleting case", error)
          return toast({
            title: "Error deleting case",
            description: `An error occurred while deleting the case: ${error.body.detail}`,
          })
      }
    },
  })

  return {
    deleteCase,
    deleteCaseIsPending,
    deleteCaseError,
  }
}

export function useCaseFields(workspaceId: string) {
  const {
    data: caseFields,
    isLoading: caseFieldsIsLoading,
    error: caseFieldsError,
  } = useQuery<CaseFieldRead[], TracecatApiError>({
    queryKey: ["case-fields", workspaceId],
    queryFn: async () => await casesListFields({ workspaceId }),
  })

  return {
    caseFields,
    caseFieldsIsLoading,
    caseFieldsError,
  }
}
export function useCaseComments({
  caseId,
  workspaceId,
}: CasesListCommentsData) {
  const {
    data: caseComments,
    isLoading: caseCommentsIsLoading,
    error: caseCommentsError,
  } = useQuery<CaseCommentRead[], TracecatApiError>({
    queryKey: ["case-comments", caseId, workspaceId],
    queryFn: async () => await casesListComments({ caseId, workspaceId }),
  })

  return {
    caseComments,
    caseCommentsIsLoading,
    caseCommentsError,
  }
}

export function useCreateCaseComment({
  caseId,
  workspaceId,
}: CasesListCommentsData) {
  const queryClient = useQueryClient()

  const {
    mutate: createComment,
    isPending: createCommentIsPending,
    error: createCommentError,
  } = useMutation({
    mutationFn: async (params: CaseCommentCreate) =>
      await casesCreateComment({
        caseId,
        workspaceId,
        requestBody: params,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["case-comments", caseId, workspaceId],
      })
    },
    onError: (error: TracecatApiError) => {
      console.error("Error creating comment", error)
      toast({
        title: "Error creating comment",
        description: `An error occurred while creating the comment: ${error.body.detail}`,
        variant: "destructive",
      })
    },
  })

  return {
    createComment,
    createCommentIsPending,
    createCommentError,
  }
}

export function useUpdateCaseComment({
  caseId,
  workspaceId,
  commentId,
}: {
  caseId: string
  workspaceId: string
  commentId: string
}) {
  const queryClient = useQueryClient()

  const {
    mutate: updateComment,
    isPending: updateCommentIsPending,
    error: updateCommentError,
  } = useMutation({
    mutationFn: async (params: CaseCommentUpdate) =>
      await casesUpdateComment({
        caseId,
        workspaceId,
        commentId,
        requestBody: params,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["case-comments", caseId, workspaceId],
      })
    },
    onError: (error: TracecatApiError) => {
      console.error("Error updating comment", error)
      toast({
        title: "Error updating comment",
        description: `An error occurred while updating the comment: ${error.body.detail}`,
        variant: "destructive",
      })
    },
  })

  return {
    updateComment,
    updateCommentIsPending,
    updateCommentError,
  }
}

export function useDeleteCaseComment({
  caseId,
  workspaceId,
  commentId,
}: {
  caseId: string
  workspaceId: string
  commentId: string
}) {
  const queryClient = useQueryClient()

  const {
    mutate: deleteComment,
    isPending: deleteCommentIsPending,
    error: deleteCommentError,
  } = useMutation({
    mutationFn: async () =>
      await casesDeleteComment({
        caseId,
        workspaceId,
        commentId,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["case-comments", caseId, workspaceId],
      })
    },
    onError: (error: TracecatApiError) => {
      console.error("Error deleting comment", error)
      toast({
        title: "Error deleting comment",
        description: `An error occurred while deleting the comment: ${error.body.detail}`,
        variant: "destructive",
      })
    },
  })

  return {
    deleteComment,
    deleteCommentIsPending,
    deleteCommentError,
  }
}

/**
 * Hook to fetch all workflows for a given workspace.
 * @param workspaceId The ID of the workspace.
 * @returns Query result for the list of workflows.
 */
export function useGetWorkflows(workspaceId: string) {
  const {
    data: workflows,
    isLoading: workflowsLoading,
    error: workflowsError,
  } = useQuery<WorkflowReadMinimal[], ApiError>({
    queryKey: ["workflows", workspaceId],
    queryFn: async () => await workflowsListWorkflows({ workspaceId }),
    retry: retryHandler,
  })

  return {
    workflows,
    workflowsLoading,
    workflowsError,
  }
}

export function useFolders(workspaceId: string) {
  const queryClient = useQueryClient()

  // List folders
  const {
    data: folders,
    isLoading: foldersIsLoading,
    error: foldersError,
  } = useQuery<WorkflowFolderRead[]>({
    queryKey: ["folders", workspaceId],
    queryFn: async () => await foldersListFolders({ workspaceId }),
  })

  // Get folder by parent path
  const {
    data: subFolders,
    isLoading: subFoldersIsLoading,
    error: subFoldersError,
    refetch: refetchSubFolders,
  } = useQuery<WorkflowFolderRead[]>({
    queryKey: ["folders", workspaceId, "parent"],
    queryFn: async () =>
      await foldersListFolders({
        workspaceId,
        parentPath: "/",
      }),
    enabled: !!workspaceId,
  })

  // Create folder
  const {
    mutateAsync: createFolder,
    isPending: createFolderIsPending,
    error: createFolderError,
  } = useMutation({
    mutationFn: async (params: WorkflowFolderCreate) =>
      await foldersCreateFolder({
        workspaceId,
        requestBody: params,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["folders", workspaceId] })
      queryClient.invalidateQueries({ queryKey: ["directory-items"] })
      toast({
        title: "Created folder",
        description: (
          <div className="flex items-center space-x-2">
            <CircleCheck className="size-4 fill-emerald-500 stroke-white" />
            <span>Folder created successfully.</span>
          </div>
        ),
      })
    },
    onError: (error: TracecatApiError) => {
      switch (error.status) {
        case 409:
          console.error("Error creating folder", error)
          return toast({
            title: "Error creating folder",
            description:
              "A folder with this name already exists at this location.",
          })
        case 403:
          return toast({
            title: "Forbidden",
            description: "You cannot perform this action",
          })
        default:
          console.error("Failed to create folder", error)
          return toast({
            title: "Failed to create folder",
            description: `An error occurred while creating the folder: ${error.body.detail}`,
          })
      }
    },
  })

  // Update folder
  const {
    mutateAsync: updateFolder,
    isPending: updateFolderIsPending,
    error: updateFolderError,
  } = useMutation({
    mutationFn: async ({
      folderId,
      name,
    }: {
      folderId: string
      name: string
    }) =>
      await foldersUpdateFolder({
        folderId,
        workspaceId,
        requestBody: { name },
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["folders", workspaceId] })
      queryClient.invalidateQueries({ queryKey: ["directory-items"] })
      toast({
        title: "Updated folder",
        description: "Folder updated successfully.",
      })
    },
    onError: (error: TracecatApiError) => {
      switch (error.status) {
        case 409:
          console.error("Error updating folder", error)
          toast({
            title: "Error updating folder",
            description:
              "A folder with this name already exists at this location.",
          })
          break
        default:
          console.error("Error updating folder", error)
          toast({
            title: "Error updating folder",
            description: `An error occurred while updating the folder: ${error.body.detail}`,
          })
          break
      }
    },
  })

  // Move folder
  const {
    mutateAsync: moveFolder,
    isPending: moveFolderIsPending,
    error: moveFolderError,
  } = useMutation({
    mutationFn: async ({
      folderId,
      newParentPath,
    }: {
      folderId: string
      newParentPath: string | null
    }) =>
      await foldersMoveFolder({
        folderId,
        workspaceId,
        requestBody: { new_parent_path: newParentPath },
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["folders", workspaceId] })
      queryClient.invalidateQueries({ queryKey: ["directory-items"] })
      toast({
        title: "Moved folder",
        description: "Folder moved successfully.",
      })
    },
    onError: (error: TracecatApiError) => {
      console.error("Error moving folder", error)
      toast({
        title: "Error moving folder",
        description: `An error occurred while moving the folder: ${error.body.detail}`,
      })
    },
  })

  // Delete folder
  const {
    mutateAsync: deleteFolder,
    isPending: deleteFolderIsPending,
    error: deleteFolderError,
  } = useMutation({
    mutationFn: async ({
      folderId,
      recursive = false,
    }: {
      folderId: string
      recursive?: boolean
    }) =>
      await foldersDeleteFolder({
        folderId,
        workspaceId,
        requestBody: { recursive },
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["folders", workspaceId] })
      queryClient.invalidateQueries({ queryKey: ["directory-items"] })
      toast({
        title: "Deleted folder",
        description: "Folder deleted successfully.",
      })
    },
    onError: (error: TracecatApiError) => {
      switch (error.status) {
        case 400:
          toast({
            title: "Cannot delete folder",
            description: String(error.body.detail),
          })
          break
        case 403:
          toast({
            title: "Forbidden",
            description: "You cannot perform this action",
          })
          break
        default:
          console.error("Error deleting folder", error)
          toast({
            title: "Failed to delete folder",
            description: `An error occurred while deleting the folder: ${error.body.detail}`,
          })
          break
      }
    },
  })

  return {
    // List
    folders,
    foldersIsLoading,
    foldersError,
    // List subfolders
    subFolders,
    subFoldersIsLoading,
    subFoldersError,
    refetchSubFolders,
    // Create
    createFolder,
    createFolderIsPending,
    createFolderError,
    // Update
    updateFolder,
    updateFolderIsPending,
    updateFolderError,
    // Move
    moveFolder,
    moveFolderIsPending,
    moveFolderError,
    // Delete
    deleteFolder,
    deleteFolderIsPending,
    deleteFolderError,
  }
}

export type DirectoryItem = FolderDirectoryItem | WorkflowDirectoryItem
export function useGetDirectoryItems(path: string, workspaceId?: string) {
  const {
    data: directoryItems,
    isLoading: directoryItemsIsLoading,
    error: directoryItemsError,
  } = useQuery<DirectoryItem[], ApiError>({
    enabled: !!workspaceId,
    queryKey: ["directory-items", path],
    queryFn: async () =>
      await foldersGetDirectory({ path, workspaceId: workspaceId ?? "" }),
  })

  return {
    directoryItems,
    directoryItemsIsLoading,
    directoryItemsError,
  }
}
