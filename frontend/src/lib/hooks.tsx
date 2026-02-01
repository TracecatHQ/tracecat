import {
  type Query,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"
import Cookies from "js-cookie"
import { AlertTriangleIcon, CircleCheck } from "lucide-react"
import { useRouter } from "next/navigation"
import { useCallback, useRef, useState } from "react"
import {
  type ActionRead,
  type ActionsDeleteActionData,
  type ActionUpdate,
  type AgentGetProviderCredentialConfigResponse,
  type AgentGetProvidersStatusResponse,
  type AgentGetWorkspaceProvidersStatusResponse,
  type AgentListModelsResponse,
  type AgentListProvidersResponse,
  type AgentSessionsListSessionsData,
  type AgentSessionsListSessionsResponse,
  type AgentSettingsRead,
  type ApiError,
  type AppSettingsRead,
  type AuditApiKeyGenerateResponse,
  type AuditSettingsRead,
  type AuthSettingsRead,
  actionsDeleteAction,
  actionsGetAction,
  actionsUpdateAction,
  agentCreateProviderCredentials,
  agentDeleteProviderCredentials,
  agentGetDefaultModel,
  agentGetProviderCredentialConfig,
  agentGetProvidersStatus,
  agentGetWorkspaceProvidersStatus,
  agentListModels,
  agentListProviderCredentialConfigs,
  agentListProviders,
  agentSessionsListSessions,
  agentSetDefaultModel,
  agentUpdateProviderCredentials,
  type CaseCommentCreate,
  type CaseCommentRead,
  type CaseCommentUpdate,
  type CaseCreate,
  type CaseDropdownDefinitionRead,
  type CaseDropdownsAddDropdownOptionData,
  type CaseDropdownsCreateDropdownDefinitionData,
  type CaseDropdownsDeleteDropdownDefinitionData,
  type CaseDropdownsDeleteDropdownOptionData,
  type CaseDropdownsReorderDropdownOptionsData,
  type CaseDropdownsUpdateDropdownDefinitionData,
  type CaseDropdownsUpdateDropdownOptionData,
  type CaseDurationDefinitionRead,
  type CaseDurationRead,
  type CaseEventsWithUsers,
  type CaseFieldReadMinimal,
  type CaseRead,
  type CaseReadMinimal,
  type CasesGetCaseData,
  type CasesListCasesData,
  type CasesListCommentsData,
  type CasesListTagsData,
  type CasesListTasksData,
  type CaseTagCreate,
  type CaseTagRead,
  type CaseTagsCreateCaseTagData,
  type CaseTagsDeleteCaseTagData,
  type CaseTagsUpdateCaseTagData,
  type CaseTaskCreate,
  type CaseTaskRead,
  type CaseTaskUpdate,
  type CaseUpdate,
  caseDropdownsAddDropdownOption,
  caseDropdownsCreateDropdownDefinition,
  caseDropdownsDeleteDropdownDefinition,
  caseDropdownsDeleteDropdownOption,
  caseDropdownsListDropdownDefinitions,
  caseDropdownsReorderDropdownOptions,
  caseDropdownsUpdateDropdownDefinition,
  caseDropdownsUpdateDropdownOption,
  casesAddTag,
  casesCreateCase,
  casesCreateComment,
  casesCreateTask,
  casesDeleteCase,
  casesDeleteComment,
  casesDeleteTask,
  casesGetCase,
  casesListCases,
  casesListComments,
  casesListEventsWithUsers,
  casesListFields,
  casesListTags,
  casesListTasks,
  casesRemoveTag,
  casesSetCaseDropdownValue,
  casesUpdateCase,
  casesUpdateComment,
  casesUpdateTask,
  caseTagsCreateCaseTag,
  caseTagsDeleteCaseTag,
  caseTagsListCaseTags,
  caseTagsUpdateCaseTag,
  type FolderDirectoryItem,
  foldersCreateFolder,
  foldersDeleteFolder,
  foldersGetDirectory,
  foldersListFolders,
  foldersMoveFolder,
  foldersUpdateFolder,
  type GitCommitInfo,
  type GitSettingsRead,
  type GraphOperation,
  graphApplyGraphOperations,
  graphGetGraph,
  type IntegrationRead,
  type IntegrationReadMinimal,
  type IntegrationUpdate,
  integrationsConnectProvider,
  integrationsDeleteIntegration,
  integrationsDisconnectIntegration,
  integrationsGetIntegration,
  integrationsListIntegrations,
  integrationsTestConnection,
  integrationsUpdateIntegration,
  type MCPIntegrationCreate,
  type MCPIntegrationRead,
  type MCPIntegrationUpdate,
  type ModelCredentialCreate,
  type ModelCredentialUpdate,
  mcpIntegrationsCreateMcpIntegration,
  mcpIntegrationsDeleteMcpIntegration,
  mcpIntegrationsGetMcpIntegration,
  mcpIntegrationsListMcpIntegrations,
  mcpIntegrationsUpdateMcpIntegration,
  type OAuthGrantType,
  type OAuthSettingsRead,
  type OrganizationDeleteOrgMemberData,
  type OrganizationDeleteSessionData,
  type OrganizationUpdateOrgMemberData,
  type OrgInvitationCreate,
  type OrgInvitationRead,
  type OrgMemberRead,
  organizationCreateInvitation,
  organizationDeleteOrgMember,
  organizationDeleteSession,
  organizationListInvitations,
  organizationListOrgMembers,
  organizationListSessions,
  organizationRevokeInvitation,
  organizationSecretsCreateOrgSecret,
  organizationSecretsDeleteOrgSecretById,
  organizationSecretsListOrgSecrets,
  organizationSecretsUpdateOrgSecretById,
  organizationUpdateOrgMember,
  type ProviderCredentialConfig,
  type ProviderRead,
  type ProviderReadMinimal,
  providersGetProvider,
  providersListProviders,
  type RegistryActionCreate,
  type RegistryActionRead,
  type RegistryActionReadMinimal,
  type RegistryActionsDeleteRegistryActionData,
  type RegistryActionsUpdateRegistryActionData,
  type RegistryRepositoriesDeleteRegistryRepositoryData,
  type RegistryRepositoriesSyncRegistryRepositoryData,
  type RegistryRepositoryErrorDetail,
  type RegistryRepositoryReadMinimal,
  registryActionsCreateRegistryAction,
  registryActionsDeleteRegistryAction,
  registryActionsGetRegistryAction,
  registryActionsListRegistryActions,
  registryActionsUpdateRegistryAction,
  registryRepositoriesDeleteRegistryRepository,
  registryRepositoriesListRegistryRepositories,
  registryRepositoriesListRepositoryCommits,
  registryRepositoriesReloadRegistryRepositories,
  registryRepositoriesSyncRegistryRepository,
  type SAMLSettingsRead,
  type ScheduleRead,
  type SchedulesCreateScheduleData,
  type SchedulesDeleteScheduleData,
  type SchedulesUpdateScheduleData,
  type SecretCreate,
  type SecretDefinition,
  type SecretReadMinimal,
  type SecretUpdate,
  type SessionRead,
  type SettingsUpdateAgentSettingsData,
  type SettingsUpdateAppSettingsData,
  type SettingsUpdateAuditSettingsData,
  type SettingsUpdateAuthSettingsData,
  type SettingsUpdateGitSettingsData,
  type SettingsUpdateOauthSettingsData,
  type SettingsUpdateSamlSettingsData,
  schedulesCreateSchedule,
  schedulesDeleteSchedule,
  schedulesListSchedules,
  schedulesUpdateSchedule,
  secretsCreateSecret,
  secretsDeleteSecretById,
  secretsListSecretDefinitions,
  secretsListSecrets,
  secretsUpdateSecretById,
  settingsGenerateAuditApiKey,
  settingsGetAgentSettings,
  settingsGetAppSettings,
  settingsGetAuditSettings,
  settingsGetAuthSettings,
  settingsGetGitSettings,
  settingsGetOauthSettings,
  settingsGetSamlSettings,
  settingsRevokeAuditApiKey,
  settingsUpdateAgentSettings,
  settingsUpdateAppSettings,
  settingsUpdateAuditSettings,
  settingsUpdateAuthSettings,
  settingsUpdateGitSettings,
  settingsUpdateOauthSettings,
  settingsUpdateSamlSettings,
  type TableRead,
  type TableReadMinimal,
  type TablesBatchInsertRowsData,
  type TablesCreateColumnData,
  type TablesCreateTableData,
  type TablesCreateTableResponse,
  type TablesDeleteColumnData,
  type TablesDeleteRowData,
  type TablesDeleteTableData,
  type TablesGetTableData,
  type TablesImportCsvData,
  type TablesImportTableFromCsvData,
  type TablesImportTableFromCsvResponse,
  type TablesInsertRowData,
  type TablesListTablesData,
  type TablesUpdateColumnData,
  type TablesUpdateRowData,
  type TablesUpdateTableData,
  type TagRead,
  type TagsCreateTagData,
  type TagsDeleteTagData,
  type TagsUpdateTagData,
  type TriggerType,
  tablesBatchInsertRows,
  tablesCreateColumn,
  tablesCreateTable,
  tablesDeleteColumn,
  tablesDeleteRow,
  tablesDeleteTable,
  tablesGetTable,
  tablesImportCsv,
  tablesImportTableFromCsv,
  tablesInsertRow,
  tablesListTables,
  tablesUpdateColumn,
  tablesUpdateRow,
  tablesUpdateTable,
  tagsCreateTag,
  tagsDeleteTag,
  tagsListTags,
  tagsUpdateTag,
  triggersDeleteWebhookApiKey,
  triggersGenerateWebhookApiKey,
  triggersRevokeWebhookApiKey,
  triggersUpdateWebhook,
  type UserUpdate,
  usersUsersPatchCurrentUser,
  type VariableCreate,
  type VariableReadMinimal,
  type VariableUpdate,
  type VcsGetGithubAppCredentialsStatusResponse,
  type VcsGetGithubAppManifestResponse,
  type VcsSaveGithubAppCredentialsData,
  type VcsSaveGithubAppCredentialsResponse,
  variablesCreateVariable,
  variablesDeleteVariableById,
  variablesListVariables,
  variablesUpdateVariableById,
  vcsDeleteGithubAppCredentials,
  vcsGetGithubAppCredentialsStatus,
  vcsGetGithubAppManifest,
  vcsSaveGithubAppCredentials,
  type WebhookUpdate,
  type WorkflowDirectoryItem,
  type WorkflowExecutionCreate,
  type WorkflowExecutionRead,
  type WorkflowExecutionReadMinimal,
  type WorkflowFolderCreate,
  type WorkflowFolderRead,
  type WorkflowReadMinimal,
  type WorkflowsAddTagData,
  type WorkflowsCreateWorkflowData,
  type WorkflowsMoveWorkflowToFolderData,
  type WorkflowsRemoveTagData,
  type WorkspaceCreate,
  type WorkspaceUpdate,
  workflowExecutionsCreateDraftWorkflowExecution,
  workflowExecutionsCreateWorkflowExecution,
  workflowExecutionsGetWorkflowExecution,
  workflowExecutionsGetWorkflowExecutionCompact,
  workflowExecutionsListWorkflowExecutions,
  workflowsAddTag,
  workflowsCreateWorkflow,
  workflowsDeleteWorkflow,
  workflowsListWorkflows,
  workflowsMoveWorkflowToFolder,
  workflowsRemoveTag,
  workspacesCreateWorkspace,
  workspacesDeleteWorkspace,
  workspacesListWorkspaces,
  workspacesUpdateWorkspace,
} from "@/client"
import {
  type CustomOAuthProviderCreateRequest,
  providersCreateCustomProvider,
} from "@/client/services.custom"
import { toast } from "@/components/ui/use-toast"
import { type AgentSessionWithStatus, enrichAgentSession } from "@/lib/agents"
import { getBaseUrl } from "@/lib/api"
import {
  listCaseDurationDefinitions,
  listCaseDurations,
} from "@/lib/case-durations"
import { invalidateCaseActivityQueries } from "@/lib/cases/invalidation"
import type { ModelInfo } from "@/lib/chat"
import { retryHandler, type TracecatApiError } from "@/lib/errors"
import type { WorkflowExecutionReadCompact } from "@/lib/event-history"
import { useWorkspaceId } from "@/providers/workspace-id"

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
      const resp = await fetch(`${getBaseUrl()}/info`)
      try {
        return await resp.json()
      } catch (_error) {
        throw new Error(
          "Unable to fetch authentication settings. This could be a network issue with the Tracecat API."
        )
      }
    },
  })
  return { appInfo, appInfoIsLoading, appInfoError }
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
        workflowId,
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

type GraphOpParams = {
  baseVersion: number
  operations: GraphOperation[]
}

/**
 * Hook to fetch graph data for a workflow.
 */
export function useGraph(workspaceId: string, workflowId: string) {
  const query = useQuery({
    queryKey: ["graph", workspaceId, workflowId],
    queryFn: async () => {
      const graph = await graphGetGraph({ workspaceId, workflowId })
      return graph
    },
    enabled: Boolean(workflowId),
  })

  return query
}

/**
 * Hook to apply graph operations with optimistic concurrency.
 * Returns the updated graph on success, allowing the canvas to update state.
 */
export function useGraphOperations(workspaceId: string, workflowId: string) {
  const queryClient = useQueryClient()

  const mutation = useMutation({
    mutationFn: async ({ baseVersion, operations }: GraphOpParams) =>
      await graphApplyGraphOperations({
        workspaceId,
        workflowId,
        requestBody: {
          base_version: baseVersion,
          operations,
        },
      }),
    onSuccess: (graph) => {
      // Update the graph cache with the new version
      queryClient.setQueryData(["graph", workspaceId, workflowId], graph)
      // Also invalidate workflow to pick up any action changes
      queryClient.invalidateQueries({ queryKey: ["workflow", workflowId] })
    },
    onError: (error) => {
      console.error("Failed to apply graph operations:", error)
    },
  })

  // Helper to refetch graph on 409 conflict
  const refetchGraph = useCallback(async () => {
    const graph = await graphGetGraph({ workspaceId, workflowId })
    queryClient.setQueryData(["graph", workspaceId, workflowId], graph)
    return graph
  }, [workspaceId, workflowId, queryClient])

  return {
    applyGraphOperations: mutation.mutateAsync,
    isPending: mutation.isPending,
    refetchGraph,
  }
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

export function useGenerateWebhookApiKey(
  workspaceId: string,
  workflowId: string
) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async () =>
      await triggersGenerateWebhookApiKey({
        workspaceId,
        workflowId,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["workflow", workflowId] })
    },
    onError: (error) => {
      console.error("Failed to generate webhook API key:", error)
      toast({
        title: "Error generating API key",
        description: "Could not generate API key. Please try again.",
      })
    },
  })
}

export function useRevokeWebhookApiKey(
  workspaceId: string,
  workflowId: string
) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async () =>
      await triggersRevokeWebhookApiKey({
        workspaceId,
        workflowId,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["workflow", workflowId] })
      toast({
        title: "API key revoked",
        description: "Webhook API key revoked successfully.",
      })
    },
    onError: (error) => {
      console.error("Failed to revoke webhook API key:", error)
      toast({
        title: "Error revoking API key",
        description: "Could not revoke API key. Please try again.",
        variant: "destructive",
      })
    },
  })
}

export function useDeleteWebhookApiKey(
  workspaceId: string,
  workflowId: string
) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async () =>
      await triggersDeleteWebhookApiKey({
        workspaceId,
        workflowId,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["workflow", workflowId] })
      toast({
        title: "API key deleted",
        description: "Webhook API key removed successfully.",
      })
    },
    onError: (error) => {
      console.error("Failed to delete webhook API key:", error)
      toast({
        title: "Error deleting API key",
        description: "Could not delete API key. Please try again.",
        variant: "destructive",
      })
    },
  })
}

interface WorkflowFilter {
  tag?: string[]
  folderId?: string
}

export function useWorkflowManager(filter?: WorkflowFilter) {
  const queryClient = useQueryClient()
  const workspaceId = useWorkspaceId()

  // List all workflows
  const {
    data: workflows,
    isLoading: workflowsLoading,
    error: workflowsError,
  } = useQuery<WorkflowReadMinimal[], ApiError>({
    queryKey: ["workflows", workspaceId, filter?.tag],
    queryFn: async () => {
      const response = await workflowsListWorkflows({
        workspaceId,
        tag: filter?.tag,
        limit: 0,
      })
      return response.items
    },
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
      queryClient.invalidateQueries({ queryKey: ["workflows", workspaceId] })
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
      queryClient.invalidateQueries({ queryKey: ["workflows", workspaceId] })
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
      queryClient.invalidateQueries({ queryKey: ["workflows", workspaceId] })
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
    staleTime: 5 * 60 * 1000, // 5 minutes
    retry: retryHandler,
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
  const getLastWorkspaceId = useCallback(
    () => Cookies.get("__tracecat:workspaces:last-viewed"),
    []
  )
  const setLastWorkspaceId = useCallback((id?: string) => {
    if (!id) {
      Cookies.set("__tracecat:workspaces:last-viewed", "")
      return
    }
    Cookies.set("__tracecat:workspaces:last-viewed", id)
  }, [])
  const clearLastWorkspaceId = useCallback(() => {
    Cookies.remove("__tracecat:workspaces:last-viewed")
  }, [])

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
  const workspaceId = useWorkspaceId()
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
    enabled?: boolean
  }
) {
  const workspaceId = useWorkspaceId()
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
    enabled: options?.enabled ?? !!executionId,
    refetchInterval: options?.refetchInterval,
  })
  return {
    execution,
    executionIsLoading,
    executionError,
  }
}

export function useCompactWorkflowExecution(workflowExecutionId?: string) {
  // if execution ID contains non-url-safe characters, decode it
  const workspaceId = useWorkspaceId()
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
  const workspaceId = useWorkspaceId()

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

export function useCreateDraftWorkflowExecution(workflowId: string) {
  const queryClient = useQueryClient()
  const workspaceId = useWorkspaceId()

  const {
    mutateAsync: createDraftExecution,
    isPending: createDraftExecutionIsPending,
    error: createDraftExecutionError,
  } = useMutation({
    mutationFn: async (params: WorkflowExecutionCreate) => {
      return await workflowExecutionsCreateDraftWorkflowExecution({
        workspaceId,
        requestBody: params,
      })
    },
    onSuccess: async ({ wf_exec_id, message }) => {
      toast({
        title: `Draft workflow run started`,
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
          console.error("Workflow validation failed", error)
          return toast({
            title: "Workflow validation failed with 1 error",
            description: "Please hover over the run button to view errors.",
          })
        default:
          console.error("Unexpected error starting draft workflow", error)
          return toast({
            title: "Unexpected error starting draft workflow",
            description: "Please check the run logs for more information",
          })
      }
    },
  })

  return {
    createDraftExecution,
    createDraftExecutionIsPending,
    createDraftExecutionError,
  }
}

export function useLastExecution({
  workflowId,
  triggerTypes,
}: {
  workflowId?: string | null
  triggerTypes: TriggerType[]
}) {
  const workspaceId = useWorkspaceId()
  const {
    data: lastExecution,
    isLoading: lastExecutionIsLoading,
    error: lastExecutionError,
  } = useQuery<WorkflowExecutionReadMinimal | null, TracecatApiError>({
    enabled: !!workflowId,
    queryKey: ["last-execution", workflowId, triggerTypes?.sort().join(",")],
    queryFn: async () => {
      const executions = await workflowExecutionsListWorkflowExecutions({
        workspaceId,
        workflowId,
        limit: 1,
        userId: "current",
        trigger: triggerTypes,
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
  const workspaceId = useWorkspaceId()
  // Fetch schedules
  const {
    data: schedules,
    isLoading,
    error,
  } = useQuery<ScheduleRead[], Error>({
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

export function useWorkspaceSecrets(workspaceId: string) {
  const queryClient = useQueryClient()
  const {
    data: secrets,
    isLoading: secretsIsLoading,
    error: secretsError,
  } = useQuery<SecretReadMinimal[], ApiError>({
    queryKey: ["workspace-secrets", workspaceId],
    queryFn: async () =>
      await secretsListSecrets({
        workspaceId,
        type: ["custom", "ssh-key", "mtls", "ca-cert"],
      }),
    enabled: !!workspaceId,
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
      queryClient.invalidateQueries({
        queryKey: ["workspace-secrets", workspaceId],
      })
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
            description:
              "Check that your secret is correctly formatted and try again.",
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
      queryClient.invalidateQueries({
        queryKey: ["workspace-secrets", workspaceId],
      })
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
      queryClient.invalidateQueries({
        queryKey: ["workspace-secrets", workspaceId],
      })
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

export function useSecretDefinitions(workspaceId: string) {
  const {
    data: secretDefinitions,
    isLoading: secretDefinitionsIsLoading,
    error: secretDefinitionsError,
  } = useQuery<SecretDefinition[], ApiError>({
    queryKey: ["secret-definitions", workspaceId],
    queryFn: async () => await secretsListSecretDefinitions({ workspaceId }),
    enabled: !!workspaceId,
  })

  return {
    secretDefinitions,
    secretDefinitionsIsLoading,
    secretDefinitionsError,
  }
}

export function useWorkspaceVariables(workspaceId: string) {
  const queryClient = useQueryClient()
  const {
    data: variables,
    isLoading: variablesIsLoading,
    error: variablesError,
  } = useQuery<VariableReadMinimal[], ApiError>({
    queryKey: ["workspace-variables", workspaceId],
    queryFn: async () =>
      await variablesListVariables({
        workspaceId,
      }),
    enabled: !!workspaceId,
  })

  // Create variable
  const { mutateAsync: createVariable } = useMutation({
    mutationFn: async (variable: VariableCreate) =>
      await variablesCreateVariable({
        workspaceId,
        requestBody: variable,
      }),
    onSuccess: () => {
      toast({
        title: "Added new variable",
        description: "New variable added successfully.",
      })
      queryClient.invalidateQueries({
        queryKey: ["workspace-variables", workspaceId],
      })
    },
    onError: (error: TracecatApiError) => {
      switch (error.status) {
        case 403:
          return toast({
            title: "Forbidden",
            description: "You cannot create variables in this workspace.",
          })
        case 409:
          return toast({
            title: "Variable already exists",
            description:
              "Variables with the same name and environment are not supported.",
          })
        default:
          console.error("Failed to create variable", error)
          return toast({
            title: "Failed to add new variable",
            description: "Please contact support for help.",
          })
      }
    },
  })

  // Update variable
  const { mutateAsync: updateVariableById } = useMutation({
    mutationFn: async ({
      variableId,
      params,
    }: {
      variableId: string
      params: VariableUpdate
    }) =>
      await variablesUpdateVariableById({
        workspaceId,
        variableId,
        requestBody: params,
      }),
    onSuccess: () => {
      toast({
        title: "Updated variable",
        description: "Variable updated successfully.",
      })
      queryClient.invalidateQueries({
        queryKey: ["workspace-variables", workspaceId],
      })
    },
    onError: (error: TracecatApiError) => {
      switch (error.status) {
        case 403:
          return toast({
            title: "Forbidden",
            description: "You cannot update variables in this workspace.",
          })
        default:
          console.error("Failed to update variable", error)
          return toast({
            title: "Failed to update variable",
            description: "An error occurred while updating the variable.",
          })
      }
    },
  })

  // Delete variable
  const { mutateAsync: deleteVariableById } = useMutation({
    mutationFn: async (variable: VariableReadMinimal) =>
      await variablesDeleteVariableById({
        workspaceId,
        variableId: variable.id,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["workspace-variables", workspaceId],
      })
      toast({
        title: "Deleted variable",
        description: "Variable deleted successfully.",
      })
    },
    onError: (error: TracecatApiError) => {
      switch (error.status) {
        case 403:
          return toast({
            title: "Forbidden",
            description: "You cannot delete variables in this workspace.",
          })
        default:
          console.error("Failed to delete variable", error)
          return toast({
            title: "Failed to delete variable",
            description: "An error occurred while deleting the variable.",
          })
      }
    },
  })

  return {
    variables,
    variablesIsLoading,
    variablesError,
    createVariable,
    updateVariableById,
    deleteVariableById,
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
export function useBuilderRegistryActions(versions?: string[]) {
  const {
    data: registryActions,
    isLoading: registryActionsIsLoading,
    error: registryActionsError,
  } = useQuery<RegistryActionReadMinimal[]>({
    queryKey: ["builder_registry_actions", versions],
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
        case 422: {
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
        }
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

export function useRepositoryCommits(
  repositoryId: string | null,
  options?: {
    branch?: string
    limit?: number
    enabled?: boolean
  }
) {
  const {
    data: commits,
    isLoading: commitsIsLoading,
    error: commitsError,
  } = useQuery<GitCommitInfo[]>({
    queryKey: [
      "repository_commits",
      repositoryId,
      options?.branch ?? "main",
      options?.limit ?? 50,
    ],
    queryFn: async () => {
      if (!repositoryId) {
        throw new Error("Repository ID is required")
      }
      return await registryRepositoriesListRepositoryCommits({
        repositoryId,
        branch: options?.branch || "main",
        limit: options?.limit || 50,
      })
    },
    enabled: options?.enabled !== false && !!repositoryId,
  })

  return {
    commits,
    commitsIsLoading,
    commitsError,
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

export function useOrgInvitations() {
  const queryClient = useQueryClient()

  const {
    data: invitations,
    isLoading,
    error,
  } = useQuery<OrgInvitationRead[]>({
    queryKey: ["org-invitations"],
    queryFn: async () => await organizationListInvitations({}),
  })

  const {
    mutateAsync: createInvitation,
    isPending: createPending,
    error: createError,
  } = useMutation({
    mutationFn: async (params: OrgInvitationCreate) =>
      await organizationCreateInvitation({ requestBody: params }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["org-invitations"] })
      toast({
        title: "Invitation created",
        description: "Invitation sent successfully.",
      })
    },
    onError: (error: TracecatApiError) => {
      const apiError = error as TracecatApiError
      const detail = apiError.body?.detail
      toast({
        title: "Failed to create invitation",
        description: typeof detail === "string" ? detail : apiError.message,
        variant: "destructive",
      })
    },
  })

  const {
    mutateAsync: revokeInvitation,
    isPending: revokePending,
    error: revokeError,
  } = useMutation({
    mutationFn: async (invitationId: string) =>
      await organizationRevokeInvitation({ invitationId }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["org-invitations"] })
      toast({
        title: "Invitation revoked",
        description: "Invitation has been revoked.",
      })
    },
    onError: (error: TracecatApiError) => {
      const apiError = error as TracecatApiError
      const detail = apiError.body?.detail
      toast({
        title: "Failed to revoke invitation",
        description: typeof detail === "string" ? detail : apiError.message,
        variant: "destructive",
      })
    },
  })

  return {
    invitations,
    isLoading,
    error,
    createInvitation,
    createPending,
    createError,
    revokeInvitation,
    revokePending,
    revokeError,
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

export function useWorkflowTags(
  workspaceId: string,
  options: { enabled: boolean } = { enabled: true }
) {
  const queryClient = useQueryClient()

  // List tags
  const {
    data: tags,
    isLoading: tagsIsLoading,
    error: tagsError,
  } = useQuery<TagRead[]>({
    queryKey: ["tags", workspaceId],
    queryFn: async () => await tagsListTags({ workspaceId }),
    enabled: options.enabled,
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

export function useCaseTagCatalog(
  workspaceId: string,
  options: { enabled: boolean } = { enabled: true }
) {
  const queryClient = useQueryClient()

  const {
    data: caseTags,
    isLoading: caseTagsIsLoading,
    error: caseTagsError,
  } = useQuery<CaseTagRead[]>({
    queryKey: ["case-tag-catalog", workspaceId],
    queryFn: async () => await caseTagsListCaseTags({ workspaceId }),
    enabled: options.enabled,
  })

  const {
    mutateAsync: createCaseTag,
    isPending: createCaseTagIsPending,
    error: createCaseTagError,
  } = useMutation({
    mutationFn: async (params: CaseTagsCreateCaseTagData) =>
      await caseTagsCreateCaseTag(params),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["case-tag-catalog", workspaceId],
      })
      queryClient.invalidateQueries({ queryKey: ["case-tags"] })
      toast({
        title: "Created case tag",
        description: (
          <div className="flex items-center space-x-2">
            <CircleCheck className="size-4 fill-emerald-500 stroke-white" />
            <span>Case tag created successfully.</span>
          </div>
        ),
      })
    },
    onError: (error: TracecatApiError) => {
      switch (error.status) {
        case 400:
          toast({
            title: "Error creating case tag",
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
          console.error("Failed to create case tag", error)
          toast({
            title: "Failed to create case tag",
            description: `An error occurred while creating the case tag: ${error.body.detail}`,
          })
      }
    },
  })

  const {
    mutateAsync: updateCaseTag,
    isPending: updateCaseTagIsPending,
    error: updateCaseTagError,
  } = useMutation({
    mutationFn: async (params: CaseTagsUpdateCaseTagData) =>
      await caseTagsUpdateCaseTag(params),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["case-tag-catalog", workspaceId],
      })
      queryClient.invalidateQueries({ queryKey: ["cases"] })
      queryClient.invalidateQueries({ queryKey: ["case-tags"] })
      toast({
        title: "Updated case tag",
        description: "Case tag updated successfully.",
      })
    },
    onError: (error: TracecatApiError) => {
      switch (error.status) {
        case 400:
          toast({
            title: "Error updating case tag",
            description: String(error.body.detail),
          })
          break
        case 403:
          toast({
            title: "Forbidden",
            description: "You cannot perform this action",
          })
          break
      }
    },
  })

  const {
    mutateAsync: deleteCaseTag,
    isPending: deleteCaseTagIsPending,
    error: deleteCaseTagError,
  } = useMutation({
    mutationFn: async (params: CaseTagsDeleteCaseTagData) =>
      await caseTagsDeleteCaseTag(params),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["case-tag-catalog", workspaceId],
      })
      queryClient.invalidateQueries({ queryKey: ["cases"] })
      queryClient.invalidateQueries({ queryKey: ["case-tags"] })
      toast({
        title: "Deleted case tag",
        description: "Case tag deleted successfully.",
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
    caseTags,
    caseTagsIsLoading,
    caseTagsError,
    createCaseTag,
    createCaseTagIsPending,
    createCaseTagError,
    updateCaseTag,
    updateCaseTagIsPending,
    updateCaseTagError,
    deleteCaseTag,
    deleteCaseTagIsPending,
    deleteCaseTagError,
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
            description: `An error occurred while updating the Git settings: ${typeof error.body.detail === "object" ? JSON.stringify(error.body.detail) : error.body.detail}`,
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

export function useGitHubAppManifest() {
  // Get GitHub App manifest
  const {
    data: manifest,
    isLoading: manifestIsLoading,
    error: manifestError,
  } = useQuery<VcsGetGithubAppManifestResponse>({
    queryKey: ["github-app-manifest"],
    queryFn: async () => await vcsGetGithubAppManifest(),
  })

  return {
    manifest,
    manifestIsLoading,
    manifestError,
  }
}

export function useGitHubAppCredentialsStatus() {
  // Get GitHub App credentials status
  const {
    data: credentialsStatus,
    isLoading: credentialsStatusIsLoading,
    error: credentialsStatusError,
    refetch: refetchCredentialsStatus,
  } = useQuery<VcsGetGithubAppCredentialsStatusResponse>({
    queryKey: ["github-app-credentials-status"],
    queryFn: async () => await vcsGetGithubAppCredentialsStatus(),
  })

  return {
    credentialsStatus,
    credentialsStatusIsLoading,
    credentialsStatusError,
    refetchCredentialsStatus,
  }
}

export function useGitHubAppCredentials() {
  const queryClient = useQueryClient()

  // Save GitHub App credentials mutation
  const saveCredentials = useMutation<
    VcsSaveGithubAppCredentialsResponse,
    ApiError,
    VcsSaveGithubAppCredentialsData["requestBody"]
  >({
    mutationFn: async (data) => {
      return await vcsSaveGithubAppCredentials({ requestBody: data })
    },
    onSuccess: () => {
      // Invalidate and refetch credentials status
      queryClient.invalidateQueries({
        queryKey: ["github-app-credentials-status"],
      })
    },
  })

  return {
    saveCredentials,
  }
}

export function useDeleteGitHubAppCredentials() {
  const queryClient = useQueryClient()

  const deleteCredentials = useMutation<void, ApiError>({
    mutationFn: async () => {
      await vcsDeleteGithubAppCredentials()
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["github-app-credentials-status"],
      })
    },
  })

  return {
    deleteCredentials,
  }
}

export function useOrgAgentSettings() {
  const queryClient = useQueryClient()
  // Get Agent settings
  const {
    data: agentSettings,
    isLoading: agentSettingsIsLoading,
    error: agentSettingsError,
  } = useQuery<AgentSettingsRead>({
    queryKey: ["org-agent-settings"],
    queryFn: async () => await settingsGetAgentSettings(),
  })

  // Update Agent settings
  const {
    mutateAsync: updateAgentSettings,
    isPending: updateAgentSettingsIsPending,
    error: updateAgentSettingsError,
  } = useMutation({
    mutationFn: async (params: SettingsUpdateAgentSettingsData) =>
      await settingsUpdateAgentSettings(params),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["org-agent-settings"] })
      toast({
        title: "Updated agent settings",
        description: "Agent settings updated successfully.",
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
          console.error("Failed to update agent settings", error)
          toast({
            title: "Failed to update agent settings",
            description: `An error occurred while updating the agent settings: ${error.body.detail}`,
          })
      }
    },
  })

  return {
    // Get
    agentSettings,
    agentSettingsIsLoading,
    agentSettingsError,
    // Update
    updateAgentSettings,
    updateAgentSettingsIsPending,
    updateAgentSettingsError,
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
        title: "Updated application settings",
        description: "Application settings updated successfully.",
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
          console.error("Failed to update application settings", error)
          toast({
            title: "Failed to update application settings",
            description: `An error occurred while updating the application settings: ${error.body.detail}`,
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

export function useOrgAuditSettings() {
  const queryClient = useQueryClient()

  // Get Audit settings
  const {
    data: auditSettings,
    isLoading: auditSettingsIsLoading,
    error: auditSettingsError,
  } = useQuery<AuditSettingsRead>({
    queryKey: ["org-audit-settings"],
    queryFn: async () => await settingsGetAuditSettings(),
  })

  // Update Audit settings
  const {
    mutateAsync: updateAuditSettings,
    isPending: updateAuditSettingsIsPending,
    error: updateAuditSettingsError,
  } = useMutation({
    mutationFn: async (params: SettingsUpdateAuditSettingsData) =>
      await settingsUpdateAuditSettings(params),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["org-audit-settings"] })
      toast({
        title: "Updated audit settings",
        description: "Audit settings updated successfully.",
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
          console.error("Failed to update audit settings", error)
          toast({
            title: "Failed to update audit settings",
            description: `An error occurred while updating the audit settings: ${error.body.detail}`,
          })
      }
    },
  })

  // Generate API Key
  const {
    mutateAsync: generateAuditApiKey,
    isPending: generateAuditApiKeyIsPending,
  } = useMutation<AuditApiKeyGenerateResponse, TracecatApiError>({
    mutationFn: async () => await settingsGenerateAuditApiKey(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["org-audit-settings"] })
      toast({
        title: "Generated API key",
        description:
          "New API key generated successfully. Make sure to copy it now.",
      })
    },
    onError: (error) => {
      switch (error.status) {
        case 403:
          toast({
            title: "Forbidden",
            description: "You cannot perform this action",
          })
          break
        default:
          console.error("Failed to generate audit API key", error)
          toast({
            title: "Failed to generate API key",
            description: `An error occurred while generating the API key: ${error.body.detail}`,
          })
      }
    },
  })

  // Revoke API Key
  const {
    mutateAsync: revokeAuditApiKey,
    isPending: revokeAuditApiKeyIsPending,
  } = useMutation<void, TracecatApiError>({
    mutationFn: async () => await settingsRevokeAuditApiKey(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["org-audit-settings"] })
      toast({
        title: "Revoked API key",
        description: "The audit webhook API key has been revoked.",
      })
    },
    onError: (error) => {
      switch (error.status) {
        case 403:
          toast({
            title: "Forbidden",
            description: "You cannot perform this action",
          })
          break
        default:
          console.error("Failed to revoke audit API key", error)
          toast({
            title: "Failed to revoke API key",
            description: `An error occurred while revoking the API key: ${error.body.detail}`,
          })
      }
    },
  })

  return {
    // Get
    auditSettings,
    auditSettingsIsLoading,
    auditSettingsError,
    // Update
    updateAuditSettings,
    updateAuditSettingsIsPending,
    updateAuditSettingsError,
    // API Key
    generateAuditApiKey,
    generateAuditApiKeyIsPending,
    revokeAuditApiKey,
    revokeAuditApiKeyIsPending,
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
        title: "Updated authentication settings",
        description: "Authentication settings updated successfully.",
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
          console.error("Failed to update authentication settings", error)
          toast({
            title: "Failed to update authentication settings",
            description: `An error occurred while updating the authentication settings: ${error.body.detail}`,
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
      console.error("Error creating table", error)
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
      queryClient.invalidateQueries({
        queryKey: [
          "rows",
          "paginated",
          variables.tableId,
          variables.workspaceId,
        ],
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
      queryClient.invalidateQueries({
        queryKey: [
          "rows",
          "paginated",
          variables.tableId,
          variables.workspaceId,
        ],
      })
    },
    onError: (error: TracecatApiError) => {
      if (error.status === 409) {
        toast({
          title: "Duplicate value error",
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

export function useUpdateRow() {
  const queryClient = useQueryClient()

  const {
    mutateAsync: updateRow,
    isPending: updateRowIsPending,
    error: updateRowError,
  } = useMutation({
    mutationFn: async (params: TablesUpdateRowData) =>
      await tablesUpdateRow(params),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({
        queryKey: ["rows", variables.tableId],
      })
      queryClient.invalidateQueries({
        queryKey: [
          "rows",
          "paginated",
          variables.tableId,
          variables.workspaceId,
        ],
      })
    },
    onError: (error: TracecatApiError) => {
      toast({
        title: "Error updating row",
        description: error.message || "An unexpected error occurred",
        variant: "destructive",
      })
    },
  })

  return {
    updateRow,
    updateRowIsPending,
    updateRowError,
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
        queryKey: [
          "rows",
          "paginated",
          variables.tableId,
          variables.workspaceId,
        ],
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
      queryClient.invalidateQueries({
        queryKey: [
          "rows",
          "paginated",
          variables.tableId,
          variables.workspaceId,
        ],
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

export function useImportTableFromCsv() {
  const queryClient = useQueryClient()
  const router = useRouter()
  const {
    mutateAsync: importTable,
    isPending: importTableIsPending,
    error: importTableError,
  } = useMutation<
    TablesImportTableFromCsvResponse,
    TracecatApiError,
    TablesImportTableFromCsvData
  >({
    mutationFn: async (params) => await tablesImportTableFromCsv(params),
    onSuccess: (response, variables) => {
      queryClient.invalidateQueries({
        queryKey: ["tables", variables.workspaceId],
      })
      toast({
        title: "Imported table successfully",
        description: "A new table has been created from the CSV file.",
      })
      if (response?.table?.id) {
        router.push(
          `/workspaces/${variables.workspaceId}/tables/${response.table.id}`
        )
      }
    },
  })

  return {
    importTable,
    importTableIsPending,
    importTableError,
  }
}

export function useListCases({ workspaceId }: CasesListCasesData) {
  const {
    data: cases,
    isLoading: casesIsLoading,
    error: casesError,
  } = useQuery<CaseReadMinimal[], TracecatApiError>({
    queryKey: ["cases", workspaceId],
    queryFn: async () => {
      const response = await casesListCases({ workspaceId })
      return response.items
    },
  })

  return {
    cases,
    casesIsLoading,
    casesError,
  }
}
interface UseGetCaseOptions {
  enabled?: boolean
}
export function useGetCase(
  { caseId, workspaceId }: CasesGetCaseData,
  options?: UseGetCaseOptions
) {
  const {
    data: caseData,
    isLoading: caseDataIsLoading,
    error: caseDataError,
  } = useQuery<CaseRead, TracecatApiError>({
    queryKey: ["case", caseId],
    queryFn: async () => await casesGetCase({ caseId, workspaceId }),
    enabled: options?.enabled,
  })

  return {
    caseData,
    caseDataIsLoading,
    caseDataError,
  }
}

export function useCreateCase(workspaceId: string) {
  const queryClient = useQueryClient()
  const {
    mutateAsync: createCase,
    isPending: createCaseIsPending,
    error: createCaseError,
  } = useMutation({
    mutationFn: async (params: CaseCreate) =>
      await casesCreateCase({ workspaceId, requestBody: params }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["cases"] })
      queryClient.invalidateQueries({ queryKey: ["cases", workspaceId] })
      // Use partial matching to invalidate all paginated queries regardless of filters
      queryClient.invalidateQueries({
        queryKey: ["cases", "paginated"],
        exact: false,
      })
      toast({
        title: "Case created",
        description: "New case created successfully",
      })
    },
    onError: (error: TracecatApiError) => {
      toast({
        title: "Error creating case",
        description: `An error occurred while creating the case: ${error.body.detail}`,
      })
    },
  })

  return {
    createCase,
    createCaseIsPending,
    createCaseError,
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
      // Use partial matching to invalidate all paginated queries regardless of filters
      queryClient.invalidateQueries({
        queryKey: ["cases", "paginated"],
        exact: false,
      })
      invalidateCaseActivityQueries(queryClient, caseId, workspaceId)
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
      // Use partial matching to invalidate all paginated queries regardless of filters
      queryClient.invalidateQueries({
        queryKey: ["cases", "paginated"],
        exact: false,
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

export function useCaseDurations({
  caseId,
  workspaceId,
  enabled = true,
}: {
  caseId: string
  workspaceId: string
  enabled: boolean
}) {
  const {
    data: caseDurations,
    isLoading: caseDurationsIsLoading,
    error: caseDurationsError,
  } = useQuery<CaseDurationRead[], TracecatApiError>({
    queryKey: ["case-durations", caseId, workspaceId],
    queryFn: async () => await listCaseDurations(workspaceId, caseId),
    enabled: Boolean(caseId && workspaceId) && enabled,
  })

  return {
    caseDurations,
    caseDurationsIsLoading,
    caseDurationsError,
  }
}

export function useCaseDurationDefinitions(
  workspaceId: string,
  enabled = true
) {
  const {
    data: caseDurationDefinitions,
    isLoading: caseDurationDefinitionsIsLoading,
    error: caseDurationDefinitionsError,
  } = useQuery<CaseDurationDefinitionRead[], Error>({
    queryKey: ["case-duration-definitions", workspaceId],
    queryFn: async () => await listCaseDurationDefinitions(workspaceId),
    enabled: Boolean(workspaceId) && enabled,
  })

  return {
    caseDurationDefinitions,
    caseDurationDefinitionsIsLoading,
    caseDurationDefinitionsError,
  }
}

export function useCaseFields(workspaceId: string) {
  const {
    data: caseFields,
    isLoading: caseFieldsIsLoading,
    error: caseFieldsError,
  } = useQuery<CaseFieldReadMinimal[], TracecatApiError>({
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

export function useCaseTasks({ caseId, workspaceId }: CasesListTasksData) {
  const {
    data: caseTasks,
    isLoading: caseTasksIsLoading,
    error: caseTasksError,
    refetch: refetchCaseTasks,
  } = useQuery<CaseTaskRead[], TracecatApiError>({
    queryKey: ["case-tasks", caseId, workspaceId],
    queryFn: async () => await casesListTasks({ caseId, workspaceId }),
  })

  return {
    caseTasks,
    caseTasksIsLoading,
    caseTasksError,
    refetchCaseTasks,
  }
}

export function useCreateCaseTask({ caseId, workspaceId }: CasesListTasksData) {
  const queryClient = useQueryClient()

  const {
    mutate: createTask,
    isPending: createTaskIsPending,
    error: createTaskError,
  } = useMutation({
    mutationFn: async (params: CaseTaskCreate) =>
      await casesCreateTask({
        caseId,
        workspaceId,
        requestBody: params,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["case-tasks", caseId, workspaceId],
      })
      invalidateCaseActivityQueries(queryClient, caseId, workspaceId)
    },
    onError: (error: TracecatApiError) => {
      console.error("Error creating task", error)
      toast({
        title: "Error creating task",
        description: `An error occurred while creating the task: ${error.body.detail}`,
      })
    },
  })

  return {
    createTask,
    createTaskIsPending,
    createTaskError,
  }
}

export function useUpdateCaseTask({
  caseId,
  workspaceId,
  taskId,
}: {
  caseId: string
  workspaceId: string
  taskId: string
}) {
  const queryClient = useQueryClient()

  const {
    mutate: updateTask,
    isPending: updateTaskIsPending,
    error: updateTaskError,
  } = useMutation({
    mutationFn: async (params: CaseTaskUpdate) =>
      await casesUpdateTask({
        caseId,
        workspaceId,
        taskId,
        requestBody: params,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["case-tasks", caseId, workspaceId],
      })
      invalidateCaseActivityQueries(queryClient, caseId, workspaceId)
    },
    onError: (error: TracecatApiError) => {
      console.error("Error updating task", error)
      toast({
        title: "Error updating task",
        description: `An error occurred while updating the task: ${error.body.detail}`,
      })
    },
  })

  return {
    updateTask,
    updateTaskIsPending,
    updateTaskError,
  }
}

export function useDeleteCaseTask({
  caseId,
  workspaceId,
  taskId,
}: {
  caseId: string
  workspaceId: string
  taskId: string
}) {
  const queryClient = useQueryClient()

  const {
    mutate: deleteTask,
    isPending: deleteTaskIsPending,
    error: deleteTaskError,
  } = useMutation({
    mutationFn: async () =>
      await casesDeleteTask({
        caseId,
        workspaceId,
        taskId,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["case-tasks", caseId, workspaceId],
      })
      invalidateCaseActivityQueries(queryClient, caseId, workspaceId)
    },
    onError: (error: TracecatApiError) => {
      console.error("Error deleting task", error)
      toast({
        title: "Error deleting task",
        description: `An error occurred while deleting the task: ${error.body.detail}`,
      })
    },
  })

  return {
    deleteTask,
    deleteTaskIsPending,
    deleteTaskError,
  }
}

export function useFolders(
  workspaceId: string,
  options: { enabled: boolean } = { enabled: true }
) {
  const queryClient = useQueryClient()

  // List folders
  const {
    data: folders,
    isLoading: foldersIsLoading,
    error: foldersError,
  } = useQuery<WorkflowFolderRead[]>({
    queryKey: ["folders", workspaceId],
    queryFn: async () => await foldersListFolders({ workspaceId }),
    enabled: options.enabled,
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
    enabled: options.enabled && !!workspaceId,
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

export function useCaseEvents({
  caseId,
  workspaceId,
}: {
  caseId: string
  workspaceId: string
}) {
  const {
    data: caseEvents,
    isLoading: caseEventsIsLoading,
    error: caseEventsError,
  } = useQuery<CaseEventsWithUsers, TracecatApiError>({
    queryKey: ["case-events", caseId, workspaceId],
    queryFn: async () =>
      await casesListEventsWithUsers({ caseId, workspaceId }),
  })

  return {
    caseEvents,
    caseEventsIsLoading,
    caseEventsError,
  }
}

export function useCaseTags({ caseId, workspaceId }: CasesListTagsData) {
  const {
    data: caseTags,
    isLoading: caseTagsIsLoading,
    error: caseTagsError,
  } = useQuery<CaseTagRead[], TracecatApiError>({
    queryKey: ["case-tags", caseId, workspaceId],
    queryFn: async () => await casesListTags({ caseId, workspaceId }),
  })

  return {
    caseTags,
    caseTagsIsLoading,
    caseTagsError,
  }
}

export function useAddCaseTag({
  caseId,
  workspaceId,
}: {
  caseId: string
  workspaceId: string
}) {
  const queryClient = useQueryClient()
  const {
    mutateAsync: addCaseTag,
    isPending: addCaseTagIsPending,
    error: addCaseTagError,
  } = useMutation({
    mutationFn: async (params: CaseTagCreate) =>
      await casesAddTag({ caseId, workspaceId, requestBody: params }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["case-tags", caseId, workspaceId],
      })
      invalidateCaseActivityQueries(queryClient, caseId, workspaceId)
    },
  })
  return {
    addCaseTag,
    addCaseTagIsPending,
    addCaseTagError,
  }
}

export function useRemoveCaseTag({
  caseId,
  workspaceId,
}: {
  caseId: string
  workspaceId: string
}) {
  const queryClient = useQueryClient()
  const {
    mutateAsync: removeCaseTag,
    isPending: removeCaseTagIsPending,
    error: removeCaseTagError,
  } = useMutation({
    mutationFn: async (tagIdentifier: string) =>
      await casesRemoveTag({ caseId, workspaceId, tagIdentifier }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["case-tags", caseId, workspaceId],
      })
      invalidateCaseActivityQueries(queryClient, caseId, workspaceId)
    },
  })
  return {
    removeCaseTag,
    removeCaseTagIsPending,
    removeCaseTagError,
  }
}

/* Integrations */
export function useIntegrations(workspaceId: string) {
  // List workspace integrations
  const {
    data: integrations,
    isLoading: integrationsIsLoading,
    error: integrationsError,
  } = useQuery<IntegrationReadMinimal[], TracecatApiError>({
    queryKey: ["integrations", workspaceId],
    queryFn: async () => await integrationsListIntegrations({ workspaceId }),
  })

  // List providers
  const {
    data: providers,
    isLoading: providersIsLoading,
    error: providersError,
  } = useQuery<ProviderReadMinimal[], TracecatApiError>({
    queryKey: ["providers", workspaceId],
    queryFn: async () => await providersListProviders({ workspaceId }),
  })

  return {
    integrations,
    integrationsIsLoading,
    integrationsError,
    providers,
    providersIsLoading,
    providersError,
  }
}

type CreateCustomProviderParams = Omit<
  CustomOAuthProviderCreateRequest,
  "provider_id"
> & {
  provider_id?: string | null
}

export function useCreateCustomProvider(workspaceId: string) {
  const queryClient = useQueryClient()

  const {
    mutateAsync: createCustomProvider,
    isPending: createCustomProviderIsPending,
    error: createCustomProviderError,
  } = useMutation({
    mutationFn: async (params: CreateCustomProviderParams) => {
      const cleanScopes = params.scopes
        ?.map((scope) => scope.trim())
        .filter(Boolean)
      const payload: CustomOAuthProviderCreateRequest = {
        ...params,
        name: params.name.trim(),
        description: params.description?.trim() || undefined,
        authorization_endpoint: params.authorization_endpoint.trim(),
        token_endpoint: params.token_endpoint.trim(),
        client_id: params.client_id.trim(),
        client_secret: params.client_secret?.trim() || undefined,
        scopes: cleanScopes ?? [],
        provider_id: params.provider_id?.trim() || undefined,
      }

      return await providersCreateCustomProvider({
        workspaceId,
        requestBody: payload,
      })
    },
    onSuccess: (provider) => {
      queryClient.invalidateQueries({ queryKey: ["providers", workspaceId] })
      toast({
        title: "Provider created",
        description: `Added ${provider.name}`,
      })
    },
    onError: (error: TracecatApiError) => {
      console.error("Failed to create custom provider:", error)
      toast({
        title: "Failed to create provider",
        description: `${error.body?.detail || error.message}`,
        variant: "destructive",
      })
    },
  })

  return {
    createCustomProvider,
    createCustomProviderIsPending,
    createCustomProviderError,
  }
}

export function useCreateMcpIntegration(workspaceId: string) {
  const queryClient = useQueryClient()

  const {
    mutateAsync: createMcpIntegration,
    isPending: createMcpIntegrationIsPending,
    error: createMcpIntegrationError,
  } = useMutation({
    mutationFn: async (params: MCPIntegrationCreate) => {
      return await mcpIntegrationsCreateMcpIntegration({
        workspaceId,
        requestBody: params,
      })
    },
    onSuccess: (integration) => {
      queryClient.invalidateQueries({
        queryKey: ["mcp-integrations", workspaceId],
      })
      toast({
        title: "MCP integration created",
        description: `Added ${integration.name}`,
      })
    },
    onError: (error: TracecatApiError) => {
      console.error("Failed to create MCP integration:", error)
      toast({
        title: "Failed to create MCP integration",
        description: `${error.body?.detail || error.message}`,
        variant: "destructive",
      })
    },
  })

  return {
    createMcpIntegration,
    createMcpIntegrationIsPending,
    createMcpIntegrationError,
  }
}

export function useListMcpIntegrations(workspaceId: string) {
  const {
    data: mcpIntegrations,
    isLoading: mcpIntegrationsIsLoading,
    error: mcpIntegrationsError,
  } = useQuery<MCPIntegrationRead[], TracecatApiError>({
    queryKey: ["mcp-integrations", workspaceId],
    queryFn: async () =>
      await mcpIntegrationsListMcpIntegrations({ workspaceId }),
    enabled: Boolean(workspaceId),
  })

  return {
    mcpIntegrations,
    mcpIntegrationsIsLoading,
    mcpIntegrationsError,
  }
}

export function useGetMcpIntegration(
  workspaceId: string,
  mcpIntegrationId: string | null
) {
  const {
    data: mcpIntegration,
    isLoading: mcpIntegrationIsLoading,
    error: mcpIntegrationError,
  } = useQuery<MCPIntegrationRead, TracecatApiError>({
    queryKey: ["mcp-integration", workspaceId, mcpIntegrationId],
    queryFn: async () =>
      await mcpIntegrationsGetMcpIntegration({
        workspaceId,
        mcpIntegrationId: mcpIntegrationId!,
      }),
    enabled: Boolean(workspaceId && mcpIntegrationId),
  })

  return {
    mcpIntegration,
    mcpIntegrationIsLoading,
    mcpIntegrationError,
  }
}

export function useUpdateMcpIntegration(workspaceId: string) {
  const queryClient = useQueryClient()

  const {
    mutateAsync: updateMcpIntegration,
    isPending: updateMcpIntegrationIsPending,
    error: updateMcpIntegrationError,
  } = useMutation({
    mutationFn: async ({
      mcpIntegrationId,
      params,
    }: {
      mcpIntegrationId: string
      params: MCPIntegrationUpdate
    }) => {
      return await mcpIntegrationsUpdateMcpIntegration({
        workspaceId,
        mcpIntegrationId,
        requestBody: params,
      })
    },
    onSuccess: (integration) => {
      queryClient.invalidateQueries({
        queryKey: ["mcp-integrations", workspaceId],
      })
      queryClient.invalidateQueries({
        queryKey: ["mcp-integration", workspaceId, integration.id],
      })
      toast({
        title: "MCP integration updated",
        description: `Updated ${integration.name}`,
      })
    },
    onError: (error: TracecatApiError) => {
      console.error("Failed to update MCP integration:", error)
      toast({
        title: "Failed to update MCP integration",
        description: `${error.body?.detail || error.message}`,
        variant: "destructive",
      })
    },
  })

  return {
    updateMcpIntegration,
    updateMcpIntegrationIsPending,
    updateMcpIntegrationError,
  }
}

export function useDeleteMcpIntegration(workspaceId: string) {
  const queryClient = useQueryClient()

  const {
    mutateAsync: deleteMcpIntegration,
    isPending: deleteMcpIntegrationIsPending,
    error: deleteMcpIntegrationError,
  } = useMutation({
    mutationFn: async (mcpIntegrationId: string) => {
      return await mcpIntegrationsDeleteMcpIntegration({
        workspaceId,
        mcpIntegrationId,
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["mcp-integrations", workspaceId],
      })
      toast({
        title: "MCP integration deleted",
        description: "The integration has been removed",
      })
    },
    onError: (error: TracecatApiError) => {
      console.error("Failed to delete MCP integration:", error)
      toast({
        title: "Failed to delete MCP integration",
        description: `${error.body?.detail || error.message}`,
        variant: "destructive",
      })
    },
  })

  return {
    deleteMcpIntegration,
    deleteMcpIntegrationIsPending,
    deleteMcpIntegrationError,
  }
}

export function useIntegrationProvider({
  providerId,
  workspaceId,
  grantType,
}: {
  providerId: string
  workspaceId: string
  grantType?: OAuthGrantType
}) {
  const queryClient = useQueryClient()

  // Read
  const {
    data: integration,
    isLoading: integrationIsLoading,
    error: integrationError,
  } = useQuery<IntegrationRead, TracecatApiError>({
    queryKey: ["integration", providerId, workspaceId, grantType],
    queryFn: async () =>
      await integrationsGetIntegration({
        providerId,
        workspaceId,
        grantType,
      }),
    retry: retryHandler,
  })

  // Get provider schema
  const {
    data: provider,
    isLoading: providerIsLoading,
    error: providerError,
  } = useQuery<ProviderRead, TracecatApiError>({
    queryKey: ["provider-schema", providerId, workspaceId, grantType],
    queryFn: async () =>
      await providersGetProvider({ providerId, workspaceId, grantType }),
  })

  // Update
  const {
    mutateAsync: updateIntegration,
    isPending: updateIntegrationIsPending,
    error: updateIntegrationError,
  } = useMutation({
    mutationFn: async (params: IntegrationUpdate) =>
      await integrationsUpdateIntegration({
        providerId,
        workspaceId,
        grantType,
        requestBody: params,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["integration", providerId, workspaceId, grantType],
      })
      queryClient.invalidateQueries({
        queryKey: ["providers", workspaceId],
      })
      queryClient.invalidateQueries({
        queryKey: ["provider-schema", providerId, workspaceId, grantType],
      })
    },
    onError: (error: TracecatApiError) => {
      console.error("Failed to update integration:", error)
      toast({
        title: "Failed to update",
        description: `Could not update integration: ${JSON.stringify(error.body?.detail) || error.message}`,
      })
    },
  })

  // Connect to provider
  const {
    mutateAsync: connectProvider,
    isPending: connectProviderIsPending,
    error: connectProviderError,
  } = useMutation({
    mutationFn: async (providerId: string) =>
      await integrationsConnectProvider({ providerId, workspaceId }),
    onSuccess: (result) => {
      // Redirect to OAuth provider if auth_url is returned
      window.location.href = result.auth_url
    },
    onError: (error: TracecatApiError) => {
      console.error("Failed to connect provider:", error)
      toast({
        title: "Failed to connect",
        description: `Could not connect to provider: ${error.body?.detail || error.message}`,
      })
    },
  })

  // Disconnect from provider
  const {
    mutateAsync: disconnectProvider,
    isPending: disconnectProviderIsPending,
    error: disconnectProviderError,
  } = useMutation({
    mutationFn: async (providerId: string) =>
      await integrationsDisconnectIntegration({ providerId, workspaceId }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["integration", providerId, workspaceId, grantType],
      })
      queryClient.invalidateQueries({
        queryKey: ["providers", workspaceId],
      })
      queryClient.invalidateQueries({
        queryKey: ["provider-schema", providerId, workspaceId, grantType],
      })
      toast({
        title: "Disconnected",
        description: "Successfully disconnected from provider",
      })
    },
    onError: (error: TracecatApiError) => {
      console.error("Failed to disconnect provider:", error)
      toast({
        title: "Failed to disconnect",
        description: `Could not disconnect from provider: ${error.body?.detail || error.message}`,
        variant: "destructive",
      })
    },
  })

  const {
    mutateAsync: deleteIntegration,
    isPending: deleteIntegrationIsPending,
    error: deleteIntegrationError,
  } = useMutation({
    mutationFn: async (providerId: string) =>
      await integrationsDeleteIntegration({
        providerId,
        workspaceId,
        grantType,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["integration", providerId, workspaceId, grantType],
      })
      queryClient.invalidateQueries({
        queryKey: ["providers", workspaceId],
      })
      queryClient.invalidateQueries({
        queryKey: ["provider-schema", providerId, workspaceId, grantType],
      })
      toast({
        title: "Connection deleted",
        description: "Removed integration configuration",
      })
    },
    onError: (error: TracecatApiError) => {
      console.error("Failed to delete integration:", error)
      toast({
        title: "Failed to delete",
        description: `Could not delete integration: ${error.body?.detail || error.message}`,
        variant: "destructive",
      })
    },
  })

  // Test connection for client credentials providers
  const {
    mutateAsync: testConnection,
    isPending: testConnectionIsPending,
    error: testConnectionError,
  } = useMutation({
    mutationFn: async (providerId: string) =>
      await integrationsTestConnection({ providerId, workspaceId }),
    onSuccess: (result) => {
      if (result.success) {
        queryClient.invalidateQueries({
          queryKey: ["integration", providerId, workspaceId, grantType],
        })
        queryClient.invalidateQueries({
          queryKey: ["providers", workspaceId],
        })
        queryClient.invalidateQueries({
          queryKey: ["provider-schema", providerId, workspaceId, grantType],
        })
        toast({
          title: "Connection successful",
          description: result.message,
        })
      } else {
        toast({
          title: "Connection failed",
          description: result.error || result.message,
          variant: "destructive",
        })
      }
    },
    onError: (error: TracecatApiError) => {
      console.error("Failed to test connection:", error)
      toast({
        title: "Test failed",
        description: `Could not test connection: ${error.body?.detail || error.message}`,
        variant: "destructive",
      })
    },
  })

  return {
    integration,
    integrationIsLoading,
    integrationError,
    updateIntegration,
    updateIntegrationIsPending,
    updateIntegrationError,
    connectProvider,
    connectProviderIsPending,
    connectProviderError,
    disconnectProvider,
    disconnectProviderIsPending,
    disconnectProviderError,
    deleteIntegration,
    deleteIntegrationIsPending,
    deleteIntegrationError,
    testConnection,
    testConnectionIsPending,
    testConnectionError,
    provider,
    providerIsLoading,
    providerError,
  }
}

// Agent hooks
interface UseAgentSessionsOptions {
  enabled?: boolean
  autoRefresh?: boolean
}

export function useAgentSessions(
  { workspaceId }: AgentSessionsListSessionsData,
  options?: UseAgentSessionsOptions
) {
  const autoRefreshEnabled = options?.autoRefresh ?? true
  /**
   * Computes the refetch interval for agent sessions based on current state.
   *
   * Returns `false` to disable polling when:
   * - Auto-refresh is disabled
   * - The browser tab is hidden
   *
   * Returns dynamic intervals based on session state:
   * - 3000ms (3s): When there are pending approvals or active sessions (RUNNING/CONTINUED_AS_NEW)
   * - 10000ms (10s): Default interval when sessions exist but are idle
   * - 10000ms (10s): When no sessions exist
   *
   * This adaptive polling reduces server load while ensuring timely updates for active workflows.
   */
  const computeRefetchInterval = useCallback(
    (
      query: Query<
        AgentSessionsListSessionsResponse,
        TracecatApiError,
        AgentSessionsListSessionsResponse,
        readonly unknown[]
      >
    ) => {
      if (!autoRefreshEnabled) {
        return false
      }

      if (
        typeof document !== "undefined" &&
        document.visibilityState === "hidden"
      ) {
        return false
      }

      const data = query.state.data

      if (!data || data.length === 0) {
        return 10000
      }

      const enrichedSessions = data.map(enrichAgentSession)

      const hasPendingApproval = enrichedSessions.some(
        (session) => session.pendingApprovalCount > 0
      )
      if (hasPendingApproval) {
        return 3000
      }

      const hasActiveSession = enrichedSessions.some((session) =>
        ["RUNNING", "CONTINUED_AS_NEW"].includes(session.derivedStatus)
      )
      if (hasActiveSession) {
        return 3000
      }

      return 10000
    },
    [autoRefreshEnabled]
  )
  const {
    data: sessions,
    isLoading: sessionsIsLoading,
    error: sessionsError,
    refetch: refetchSessions,
  } = useQuery<
    AgentSessionsListSessionsResponse,
    TracecatApiError,
    AgentSessionWithStatus[]
  >({
    queryKey: ["agent-sessions", workspaceId],
    queryFn: async () => await agentSessionsListSessions({ workspaceId }),
    select: (data) => data.map(enrichAgentSession),
    enabled: options?.enabled ?? Boolean(workspaceId),
    retry: retryHandler,
    refetchInterval: computeRefetchInterval,
  })

  return {
    sessions,
    sessionsIsLoading,
    sessionsError,
    refetchSessions,
  }
}

export function useAgentModels() {
  const {
    data: models,
    isLoading: modelsLoading,
    error: modelsError,
  } = useQuery<AgentListModelsResponse, ApiError>({
    queryKey: ["agent-models"],
    queryFn: async () => await agentListModels(),
  })

  return {
    models,
    modelsLoading,
    modelsError,
  }
}

export function useModelProviders() {
  const {
    data: providers,
    isLoading,
    error,
  } = useQuery<AgentListProvidersResponse>({
    queryKey: ["agent-providers"],
    queryFn: async () => await agentListProviders(),
  })

  return {
    providers,
    isLoading,
    error,
  }
}

export function useModelProvidersStatus() {
  const {
    data: providersStatus,
    isLoading,
    error,
    refetch,
  } = useQuery<AgentGetProvidersStatusResponse>({
    queryKey: ["agent-providers-status"],
    queryFn: async () => await agentGetProvidersStatus(),
  })

  return {
    providersStatus,
    isLoading,
    error,
    refetch,
  }
}

export function useWorkspaceModelProvidersStatus(workspaceId: string) {
  const {
    data: providersStatus,
    isLoading,
    error,
    refetch,
  } = useQuery<AgentGetWorkspaceProvidersStatusResponse>({
    queryKey: ["workspace-agent-providers-status", workspaceId],
    queryFn: async () =>
      await agentGetWorkspaceProvidersStatus({ workspaceId }),
  })

  return {
    providersStatus,
    isLoading,
    error,
    refetch,
  }
}

export function useAgentDefaultModel() {
  const queryClient = useQueryClient()

  // Get default model
  const {
    data: defaultModel,
    isLoading: defaultModelLoading,
    error: defaultModelError,
  } = useQuery<string | null>({
    queryKey: ["agent-default-model"],
    queryFn: async () => await agentGetDefaultModel(),
  })

  // Update default model
  const {
    mutateAsync: updateDefaultModel,
    isPending: isUpdating,
    error: updateError,
  } = useMutation({
    mutationFn: async (modelName: string) =>
      await agentSetDefaultModel({
        modelName,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agent-default-model"] })
    },
  })

  return {
    defaultModel,
    defaultModelLoading,
    defaultModelError,
    updateDefaultModel,
    isUpdating,
    updateError,
  }
}

export function useAgentCredentials() {
  const queryClient = useQueryClient()

  // Create credentials
  const {
    mutateAsync: createCredentials,
    isPending: isCreating,
    error: createError,
  } = useMutation({
    mutationFn: async (data: ModelCredentialCreate) =>
      await agentCreateProviderCredentials({
        requestBody: data,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agent-providers-status"] })
    },
  })

  // Update credentials
  const {
    mutateAsync: updateCredentials,
    isPending: isUpdating,
    error: updateError,
  } = useMutation({
    mutationFn: async ({
      provider,
      credentials,
    }: {
      provider: string
      credentials: ModelCredentialUpdate
    }) =>
      await agentUpdateProviderCredentials({
        provider,
        requestBody: credentials,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agent-providers-status"] })
    },
  })

  // Delete credentials
  const {
    mutateAsync: deleteCredentials,
    isPending: isDeleting,
    error: deleteError,
  } = useMutation({
    mutationFn: async (provider: string) =>
      await agentDeleteProviderCredentials({
        provider,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agent-providers-status"] })
    },
  })

  return {
    createCredentials,
    updateCredentials,
    deleteCredentials,
    isCreating,
    isUpdating,
    isDeleting,
    createError,
    updateError,
    deleteError,
  }
}

export function useProviderCredentialConfigs() {
  const {
    data: providerConfigs,
    isLoading: providerConfigsLoading,
    error: providerConfigsError,
  } = useQuery<ProviderCredentialConfig[]>({
    queryKey: ["agent-provider-credential-configs"],
    queryFn: async () => await agentListProviderCredentialConfigs(),
  })

  return {
    providerConfigs,
    providerConfigsLoading,
    providerConfigsError,
  }
}

export function useProviderCredentialConfig(provider: string | null) {
  const {
    data: providerConfig,
    isLoading: providerConfigLoading,
    error: providerConfigError,
  } = useQuery<AgentGetProviderCredentialConfigResponse>({
    queryKey: ["agent-provider-credential-config", provider],
    queryFn: async () => {
      if (!provider) {
        throw new Error("Provider is required")
      }
      return await agentGetProviderCredentialConfig({ provider })
    },
    enabled: !!provider,
  })

  return {
    providerConfig,
    providerConfigLoading,
    providerConfigError,
  }
}

export function useDeleteProviderCredentials() {
  const queryClient = useQueryClient()

  const mutation = useMutation<void, ApiError, string>({
    mutationFn: async (provider: string) => {
      await agentDeleteProviderCredentials({ provider })
    },
    onSuccess: () => {
      // Invalidate and refetch provider status
      queryClient.invalidateQueries({
        queryKey: ["agent-providers-status"],
      })
    },
  })

  return {
    deleteProviderCredentials: mutation.mutate,
    isDeletingCredentials: mutation.isPending,
    deleteCredentialsError: mutation.error,
  }
}

/**
 * Are we ready to chat?
 * Returns { ready, reason, modelInfo }
 *  ready   – boolean
 *  reason  – "no_model" | "no_credentials" | null
 *  modelInfo – model info (if any)
 */

interface ChatReadinessOptions {
  modelOverride?: {
    name: string
    provider: string
    baseUrl?: string | null
  }
}

export function useChatReadiness(options?: ChatReadinessOptions) {
  const { defaultModel, defaultModelLoading } = useAgentDefaultModel()
  const { models, modelsLoading } = useAgentModels()
  const { providersStatus, isLoading: statusLoading } =
    useModelProvidersStatus()
  const modelOverride = options?.modelOverride

  const loading = defaultModelLoading || modelsLoading || statusLoading

  if (loading) {
    return {
      ready: false,
      loading: true,
    }
  }

  if (modelOverride) {
    const modelInfo: ModelInfo = {
      name: modelOverride.name,
      provider: modelOverride.provider,
      baseUrl: modelOverride.baseUrl ?? null,
    }
    const hasOverrideCreds = providersStatus?.[modelOverride.provider] ?? false
    if (!hasOverrideCreds) {
      return {
        ready: false,
        loading: false,
        reason: "no_credentials",
        modelInfo,
      }
    }

    return {
      ready: true,
      loading: false,
      modelInfo,
    }
  }

  /* no default model set */
  if (!defaultModel) {
    return {
      ready: false,
      loading: false,
      reason: "no_model",
    }
  }

  /* unknown model name → treat as no model */
  const modelCfg = models?.[defaultModel]
  if (!modelCfg) {
    return {
      ready: false,
      loading: false,
      reason: "no_model",
    }
  }

  /* check provider creds */
  const providerId = modelCfg.provider
  const hasCreds = providersStatus?.[providerId] ?? false
  const modelInfo: ModelInfo = {
    name: defaultModel,
    provider: providerId,
    baseUrl: null,
  }
  if (!hasCreds) {
    return {
      ready: false,
      loading: false,
      reason: "no_credentials",
      modelInfo,
    }
  }

  return {
    ready: true,
    loading: false,
    modelInfo,
  }
}

interface UseDragDividerOptions {
  /** Current size (width for vertical divider, height for horizontal) in pixels */
  value: number
  /** Callback fired with the new size while dragging */
  onChange: (newSize: number) => void
  /** Orientation of the divider. Defaults to "vertical" (i.e. a vertical bar that resizes width) */
  orientation?: "vertical" | "horizontal"
  /** Minimum size constraint in pixels */
  min?: number
  /** Maximum size constraint in pixels */
  max?: number
}

/**
 * Hook for handling drag divider logic.
 * Returns mouse event handlers to be attached to the drag handle element.
 *
 * This implementation avoids useEffect and global event listeners by using
 * React's onMouseDown/onMouseMove/onMouseUp events with pointer capture.
 */
export function useDragDivider({
  value,
  onChange,
  orientation = "vertical",
  min = 400,
  max = 600,
}: UseDragDividerOptions) {
  const [isDragging, setIsDragging] = useState(false)
  const dragStartRef = useRef({ coord: 0, size: 0 })

  const handlePointerDown = useCallback(
    (e: React.PointerEvent<HTMLElement>) => {
      e.preventDefault()

      // Capture the pointer to receive all mouse events
      e.currentTarget.setPointerCapture(e.pointerId)

      const startCoord = orientation === "vertical" ? e.clientX : e.clientY
      dragStartRef.current = { coord: startCoord, size: value }
      setIsDragging(true)
    },
    [orientation, value]
  )

  const handlePointerMove = useCallback(
    (e: React.PointerEvent<HTMLElement>) => {
      if (!isDragging) return

      const currentCoord = orientation === "vertical" ? e.clientX : e.clientY
      const delta = dragStartRef.current.coord - currentCoord
      const newSize = Math.min(
        Math.max(dragStartRef.current.size + delta, min),
        max
      )
      onChange(newSize)
    },
    [isDragging, orientation, min, max, onChange]
  )

  const handlePointerUp = useCallback(
    (e: React.PointerEvent<HTMLElement>) => {
      if (!isDragging) return

      // Release the pointer capture
      e.currentTarget.releasePointerCapture(e.pointerId)
      setIsDragging(false)
    },
    [isDragging]
  )

  // Handle cases where the pointer is lost (e.g., window loses focus)
  const handlePointerCancel = useCallback(
    (e: React.PointerEvent<HTMLElement>) => {
      if (!isDragging) return

      e.currentTarget.releasePointerCapture(e.pointerId)
      setIsDragging(false)
    },
    [isDragging]
  )

  return {
    isDragging,
    dragHandleProps: {
      onPointerDown: handlePointerDown,
      onPointerMove: handlePointerMove,
      onPointerUp: handlePointerUp,
      onPointerCancel: handlePointerCancel,
      style: {
        cursor: orientation === "vertical" ? "col-resize" : "row-resize",
      },
    },
  }
}

export function useWorkspaceSettings(
  workspaceId: string,
  onWorkspaceDeleted?: () => void
) {
  const queryClient = useQueryClient()

  // Update workspace
  const { mutateAsync: updateWorkspace, isPending: isUpdating } = useMutation({
    mutationFn: async (params: WorkspaceUpdate) => {
      return await workspacesUpdateWorkspace({
        workspaceId,
        requestBody: params,
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["workspace", workspaceId] })
      queryClient.invalidateQueries({ queryKey: ["workspaces"] })
      toast({
        title: "Workspace updated",
        description: "Workspace settings updated successfully.",
      })
    },
    onError: (error) => {
      console.error("Failed to update workspace", error)
      toast({
        title: "Error updating workspace",
        description: "Failed to update the workspace. Please try again.",
        variant: "destructive",
      })
    },
  })

  // Delete workspace
  const { mutateAsync: deleteWorkspace, isPending: isDeleting } = useMutation({
    mutationFn: async () => {
      return await workspacesDeleteWorkspace({
        workspaceId,
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["workspaces"] })
      toast({
        title: "Workspace deleted",
        description: "The workspace has been deleted successfully.",
      })
      onWorkspaceDeleted?.()
    },
    onError: (error) => {
      console.error("Failed to delete workspace", error)
      toast({
        title: "Error deleting workspace",
        description: "Failed to delete the workspace. Please try again.",
        variant: "destructive",
      })
    },
  })

  return {
    updateWorkspace,
    isUpdating,
    deleteWorkspace,
    isDeleting,
  }
}

export function useCaseDropdownDefinitions(workspaceId: string) {
  const queryClient = useQueryClient()

  const {
    data: dropdownDefinitions,
    isLoading: dropdownDefinitionsIsLoading,
    error: dropdownDefinitionsError,
  } = useQuery<CaseDropdownDefinitionRead[], Error>({
    queryKey: ["case-dropdown-definitions", workspaceId],
    queryFn: async () =>
      await caseDropdownsListDropdownDefinitions({ workspaceId }),
    enabled: Boolean(workspaceId),
  })

  const invalidate = () =>
    queryClient.invalidateQueries({
      queryKey: ["case-dropdown-definitions", workspaceId],
    })

  const { mutateAsync: createDropdownDefinition } = useMutation({
    mutationFn: async (data: CaseDropdownsCreateDropdownDefinitionData) =>
      await caseDropdownsCreateDropdownDefinition(data),
    onSuccess: invalidate,
  })

  const {
    mutateAsync: deleteDropdownDefinition,
    isPending: deleteDropdownDefinitionIsPending,
  } = useMutation({
    mutationFn: async (data: CaseDropdownsDeleteDropdownDefinitionData) =>
      await caseDropdownsDeleteDropdownDefinition(data),
    onSuccess: invalidate,
  })

  const {
    mutateAsync: updateDropdownDefinition,
    isPending: updateDropdownDefinitionIsPending,
  } = useMutation({
    mutationFn: async (data: CaseDropdownsUpdateDropdownDefinitionData) =>
      await caseDropdownsUpdateDropdownDefinition(data),
    onSuccess: invalidate,
  })

  const { mutateAsync: addDropdownOption } = useMutation({
    mutationFn: async (data: CaseDropdownsAddDropdownOptionData) =>
      await caseDropdownsAddDropdownOption(data),
    onSuccess: invalidate,
  })

  const { mutateAsync: updateDropdownOption } = useMutation({
    mutationFn: async (data: CaseDropdownsUpdateDropdownOptionData) =>
      await caseDropdownsUpdateDropdownOption(data),
    onSuccess: invalidate,
  })

  const { mutateAsync: deleteDropdownOption } = useMutation({
    mutationFn: async (data: CaseDropdownsDeleteDropdownOptionData) =>
      await caseDropdownsDeleteDropdownOption(data),
    onSuccess: invalidate,
  })

  const { mutateAsync: reorderDropdownOptions } = useMutation({
    mutationFn: async (data: CaseDropdownsReorderDropdownOptionsData) =>
      await caseDropdownsReorderDropdownOptions(data),
    onSuccess: invalidate,
  })

  return {
    dropdownDefinitions,
    dropdownDefinitionsIsLoading,
    dropdownDefinitionsError,
    createDropdownDefinition,
    deleteDropdownDefinition,
    deleteDropdownDefinitionIsPending,
    updateDropdownDefinition,
    updateDropdownDefinitionIsPending,
    addDropdownOption,
    updateDropdownOption,
    deleteDropdownOption,
    reorderDropdownOptions,
  }
}

export function useSetCaseDropdownValue(workspaceId: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async ({
      caseId,
      definitionId,
      optionId,
    }: {
      caseId: string
      definitionId: string
      optionId: string | null
    }) =>
      await casesSetCaseDropdownValue({
        caseId,
        definitionId,
        workspaceId,
        requestBody: { option_id: optionId },
      }),
    onSuccess: (_data, variables) => {
      invalidateCaseActivityQueries(queryClient, variables.caseId, workspaceId)
    },
  })
}
