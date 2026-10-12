import {
  type ApiError,
  type FormCreate,
  type FormRead,
  type FormReadMinimal,
  type FormResolved,
  type FormSubmissionCreate,
  type FormSubmissionRead,
  type FormSubmissionReceipt,
  type FormsListFormSubmissionsResponse,
  type FormsSearchFormUsersResponse,
  type FormUpdate,
  type FormUserRead,
  type FormValidateRequest,
  type FormValidateResponse,
  formsCreateForm,
  formsDeleteForm,
  formsGetForm,
  formsListFormSubmissions,
  formsListForms,
  formsRenderForm,
  formsSearchFormUsers,
  formsSubmitForm,
  formsUpdateForm,
  formsValidateForm,
} from "@/client"
import { requesterFormKeys } from "@/hooks/use-requester-forms"
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@/lib/query"

const USER_SEARCH_PAGE_SIZE = 20
const FORMS_PAGE_SIZE = 200
const SUBMISSIONS_PAGE_SIZE = 50
const EMPTY_USERS: readonly FormUserRead[] = []
const EMPTY_FORMS: readonly FormReadMinimal[] = []
const EMPTY_SUBMISSIONS: readonly FormSubmissionRead[] = []

/**
 * Query keys of the workspace-scoped form queries.
 *
 * `detail` is a prefix of `rendered` and `users`. Pass `exact: true` when
 * invalidating it, or an open requester page re-renders its form and records
 * another view.
 */
export const formKeys = {
  list: (workspaceId: string) => ["forms", workspaceId] as const,
  detail: (workspaceId: string, formId: string) =>
    ["form", workspaceId, formId] as const,
  rendered: (workspaceId: string, formId: string) =>
    ["form", workspaceId, formId, "render"] as const,
  users: (workspaceId: string, formId: string, search: string) =>
    ["form", workspaceId, formId, "users", search] as const,
  submissions: (workspaceId: string, formId: string) =>
    ["form-submissions", workspaceId, formId] as const,
}

/** Identifies one form in one workspace. */
export interface FormScope {
  workspaceId: string
  formId: string
}

/**
 * Fetch an online form as the blocks a client renders.
 *
 * The server records a view on every call, so this query never refetches on
 * its own: not on window focus, not on reconnect and not on remount. Call
 * `refetch` to reload it on purpose, for example after `form_changed`.
 *
 * Fails with 409 `form_offline` or `form_unavailable` and with 404.
 */
export function useRenderedForm(
  { workspaceId, formId }: Partial<FormScope>,
  options: { enabled?: boolean } = {}
) {
  const query = useQuery<FormResolved, ApiError>({
    queryKey: formKeys.rendered(workspaceId ?? "", formId ?? ""),
    queryFn: () =>
      formsRenderForm({
        workspaceId: workspaceId as string,
        formId: formId as string,
      }),
    enabled: (options.enabled ?? true) && Boolean(workspaceId && formId),
    staleTime: Number.POSITIVE_INFINITY,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchOnMount: false,
    retry: false,
    meta: { suppressErrorToast: true },
  })
  return {
    form: query.data,
    formIsLoading: query.isLoading,
    formIsFetching: query.isFetching,
    formError: query.error,
    refetch: query.refetch,
  }
}

/**
 * Submit a form. Resolves to the requester's receipt.
 *
 * Always pass the `definition_version` of the form that was rendered, so a
 * form that changed underneath the requester is rejected with `form_changed`
 * instead of being read against the wrong inputs.
 *
 * The caller renders failures inline: read them with `getFormErrorDetail`.
 */
export function useSubmitForm({ workspaceId, formId }: FormScope) {
  const queryClient = useQueryClient()
  const mutation = useMutation<
    FormSubmissionReceipt,
    ApiError,
    FormSubmissionCreate
  >({
    mutationFn: (requestBody) =>
      formsSubmitForm({ workspaceId, formId, requestBody }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: requesterFormKeys.submissions })
      queryClient.invalidateQueries({
        queryKey: formKeys.submissions(workspaceId, formId),
      })
      queryClient.invalidateQueries({ queryKey: formKeys.list(workspaceId) })
    },
    meta: { suppressErrorToast: true },
  })
  return {
    submitForm: mutation.mutateAsync,
    submitFormIsPending: mutation.isPending,
  }
}

/**
 * Every form in a workspace, newest first.
 *
 * Callers without `form:update` only get online forms. All pages are loaded
 * so the list can be searched and the new-form dialog can tell which
 * workflows already have a form; a workspace has at most one form per
 * workflow.
 */
export function useForms(
  workspaceId: string,
  options: { enabled?: boolean } = {}
) {
  const query = useQuery<FormReadMinimal[], ApiError>({
    queryKey: formKeys.list(workspaceId),
    queryFn: async () => {
      const forms: FormReadMinimal[] = []
      let cursor: string | null = null
      do {
        const page = await formsListForms({
          workspaceId,
          limit: FORMS_PAGE_SIZE,
          cursor,
        })
        forms.push(...page.items)
        cursor = page.has_more ? (page.next_cursor ?? null) : null
      } while (cursor)
      return forms
    },
    enabled: (options.enabled ?? true) && Boolean(workspaceId),
  })
  return {
    forms: query.data ?? EMPTY_FORMS,
    formsIsLoading: query.isLoading,
    formsError: query.error,
  }
}

/**
 * A form's config, issues, workflow and counts, for the builder.
 *
 * Fails with 404 for an unknown form and for a caller who holds neither
 * `form:update` nor `form:submission:read`.
 */
export function useFormDetail(
  { workspaceId, formId }: Partial<FormScope>,
  options: { enabled?: boolean } = {}
) {
  const query = useQuery<FormRead, ApiError>({
    queryKey: formKeys.detail(workspaceId ?? "", formId ?? ""),
    queryFn: () =>
      formsGetForm({
        workspaceId: workspaceId as string,
        formId: formId as string,
      }),
    enabled: (options.enabled ?? true) && Boolean(workspaceId && formId),
    retry: false,
    meta: { suppressErrorToast: true },
  })
  return {
    form: query.data,
    formIsLoading: query.isLoading,
    formError: query.error,
  }
}

/**
 * Create the form of a workflow. Fails with 409 when the workflow already
 * has one. The caller shows failures.
 */
export function useCreateForm(workspaceId: string) {
  const queryClient = useQueryClient()
  const mutation = useMutation<FormRead, ApiError, FormCreate>({
    mutationFn: (requestBody) => formsCreateForm({ workspaceId, requestBody }),
    onSuccess: (form) => {
      queryClient.setQueryData(formKeys.detail(workspaceId, form.id), form)
      queryClient.invalidateQueries({ queryKey: formKeys.list(workspaceId) })
    },
    meta: { suppressErrorToast: true },
  })
  return {
    createForm: mutation.mutateAsync,
    createFormIsPending: mutation.isPending,
  }
}

/**
 * Update a form's status, config or both. The config is replaced whole.
 *
 * The caller shows failures: read a rejected config with
 * `getFormConfigIssues` and anything else with `getFormErrorDetail`.
 */
export function useUpdateForm({ workspaceId, formId }: FormScope) {
  const queryClient = useQueryClient()
  const mutation = useMutation<FormRead, ApiError, FormUpdate>({
    mutationFn: (requestBody) =>
      formsUpdateForm({ workspaceId, formId, requestBody }),
    onSuccess: (form) => {
      queryClient.setQueryData(formKeys.detail(workspaceId, formId), form)
      queryClient.invalidateQueries({ queryKey: formKeys.list(workspaceId) })
    },
    meta: { suppressErrorToast: true },
  })
  return {
    updateForm: mutation.mutateAsync,
    updateFormIsPending: mutation.isPending,
  }
}

/** Delete a form with its submissions and activity. */
export function useDeleteForm(workspaceId: string) {
  const queryClient = useQueryClient()
  const mutation = useMutation<void, ApiError, string>({
    mutationFn: (formId) => formsDeleteForm({ workspaceId, formId }),
    onSuccess: (_result, formId) => {
      queryClient.removeQueries({
        queryKey: formKeys.detail(workspaceId, formId),
      })
      queryClient.removeQueries({
        queryKey: formKeys.submissions(workspaceId, formId),
      })
      queryClient.invalidateQueries({ queryKey: formKeys.list(workspaceId) })
    },
  })
  return {
    deleteForm: mutation.mutateAsync,
    deleteFormIsPending: mutation.isPending,
  }
}

/**
 * Resolve an unsaved config against a workflow's inputs.
 *
 * A mutation, not a query: a config can hold a megabyte of inline images
 * and must never become part of a query key.
 */
export function useValidateFormConfig(workspaceId: string) {
  const mutation = useMutation<
    FormValidateResponse,
    ApiError,
    FormValidateRequest
  >({
    mutationFn: (requestBody) =>
      formsValidateForm({ workspaceId, requestBody }),
    meta: { suppressErrorToast: true },
  })
  return { validateFormConfig: mutation.mutateAsync }
}

/**
 * A form's submissions with answers, submitter and run status, newest
 * first, a page at a time. Needs `form:submission:read`.
 */
export function useFormSubmissions(
  { workspaceId, formId }: FormScope,
  options: { enabled?: boolean } = {}
) {
  const query = useInfiniteQuery<
    FormsListFormSubmissionsResponse,
    ApiError,
    { pages: FormsListFormSubmissionsResponse[] },
    readonly unknown[],
    string | null
  >({
    queryKey: formKeys.submissions(workspaceId, formId),
    queryFn: ({ pageParam }) =>
      formsListFormSubmissions({
        workspaceId,
        formId,
        limit: SUBMISSIONS_PAGE_SIZE,
        cursor: pageParam,
      }),
    initialPageParam: null,
    getNextPageParam: (lastPage) =>
      lastPage.has_more ? (lastPage.next_cursor ?? undefined) : undefined,
    enabled: (options.enabled ?? true) && Boolean(workspaceId && formId),
    meta: { suppressErrorToast: true },
  })
  return {
    submissions:
      query.data?.pages.flatMap((page) => page.items) ?? EMPTY_SUBMISSIONS,
    submissionsIsLoading: query.isLoading,
    submissionsError: query.error,
    hasNextPage: query.hasNextPage,
    isFetchingNextPage: query.isFetchingNextPage,
    fetchNextPage: query.fetchNextPage,
    refetch: query.refetch,
  }
}

/**
 * Search organization members for a form's `user` field, a page at a time.
 *
 * The server returns 404 when the saved form has no `user` field and the
 * caller cannot edit the form. That reads as no results.
 */
export function useFormUserSearch(
  { workspaceId, formId }: FormScope,
  search: string,
  options: { enabled?: boolean } = {}
) {
  const term = search.trim()
  const query = useInfiniteQuery<
    FormsSearchFormUsersResponse,
    ApiError,
    { pages: FormsSearchFormUsersResponse[] },
    readonly unknown[],
    string | null
  >({
    queryKey: formKeys.users(workspaceId, formId, term),
    queryFn: ({ pageParam }) =>
      formsSearchFormUsers({
        workspaceId,
        formId,
        search: term || undefined,
        limit: USER_SEARCH_PAGE_SIZE,
        cursor: pageParam,
      }),
    initialPageParam: null,
    getNextPageParam: (lastPage) =>
      lastPage.has_more ? (lastPage.next_cursor ?? undefined) : undefined,
    enabled: (options.enabled ?? true) && Boolean(workspaceId && formId),
    retry: false,
    meta: { suppressErrorToast: true },
  })
  return {
    users: query.data?.pages.flatMap((page) => page.items) ?? EMPTY_USERS,
    usersIsLoading: query.isLoading,
    usersError: query.error,
    hasNextPage: query.hasNextPage,
    isFetchingNextPage: query.isFetchingNextPage,
    fetchNextPage: query.fetchNextPage,
  }
}
