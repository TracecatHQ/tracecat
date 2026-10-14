import {
  type ApiError,
  type FormCreate,
  type FormIntakeLocation,
  type FormPreview,
  type FormRead,
  type FormReadMinimal,
  type FormSubmissionCreate,
  type FormSubmissionReceipt,
  type FormUpdate,
  formsCreateForm,
  formsDeleteForm,
  formsGetForm,
  formsListFormSubmissions,
  formsListForms,
  formsListIntakeForms,
  formsListIntakeSubmissions,
  formsLocateIntakeForm,
  formsRenderForm,
  formsSearchFormUsers,
  formsSubmitForm,
  formsUpdateForm,
} from "@/client"
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@/lib/query"

const PAGE_SIZE = 50
const FORMS_PAGE_SIZE = 200
const USER_SEARCH_PAGE_SIZE = 20
const NO_ITEMS: never[] = []
const NO_FORMS: FormReadMinimal[] = []

/** Query keys shared by more than one forms query or mutation. */
export const formKeys = {
  list: (workspaceId: string) => ["forms", workspaceId] as const,
  detail: (workspaceId: string, formId: string) =>
    ["form", workspaceId, formId] as const,
  submissions: (workspaceId: string, formId: string) =>
    ["form-submissions", workspaceId, formId] as const,
  mySubmissions: ["requester-submissions"] as const,
}

/** Identifies one form in one workspace. */
export interface FormScope {
  workspaceId: string
  formId: string
}

interface CursorPage<T> {
  items: T[]
  next_cursor?: string | null
  has_more?: boolean
}

/** A cursor-paginated list loaded a page at a time, for "Load more". */
function useCursorList<T>(
  queryKey: readonly unknown[],
  fetchPage: (cursor: string | null) => Promise<CursorPage<T>>,
  { enabled = true, retry }: { enabled?: boolean; retry?: false } = {}
) {
  const query = useInfiniteQuery({
    queryKey,
    queryFn: ({ pageParam }) => fetchPage(pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (page) =>
      page.has_more ? (page.next_cursor ?? undefined) : undefined,
    enabled,
    retry,
    meta: { suppressErrorToast: true },
  })
  return {
    items: query.data?.pages.flatMap((page) => page.items) ?? (NO_ITEMS as T[]),
    isLoading: query.isLoading,
    error: query.error,
    hasNextPage: query.hasNextPage,
    isFetchingNextPage: query.isFetchingNextPage,
    fetchNextPage: query.fetchNextPage,
    refetch: query.refetch,
  }
}

/** What {@link useCursorList} returns, for components that render one. */
export type CursorList<T> = ReturnType<typeof useCursorList<T>>

/**
 * Every form in a workspace, newest first, with all pages loaded so the list
 * can be searched and matched to workflows.
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
    forms: query.data ?? NO_FORMS,
    formsIsLoading: query.isLoading,
    formsError: query.error,
  }
}

/** A form's config, issues, workflow and counts, for the builder. */
export function useFormDetail(
  { workspaceId, formId }: Partial<FormScope>,
  options: { enabled?: boolean } = {}
) {
  return useQuery<FormRead, ApiError>({
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
}

/**
 * An online form as the blocks a requester fills in. The server records a
 * view on every call, so this never refetches on its own; call `refetch` to
 * reload on purpose, as after `form_changed`.
 */
export function useRenderedForm({ workspaceId, formId }: Partial<FormScope>) {
  return useQuery<FormPreview, ApiError>({
    queryKey: ["form-render", workspaceId, formId],
    queryFn: () =>
      formsRenderForm({
        workspaceId: workspaceId as string,
        formId: formId as string,
      }),
    enabled: Boolean(workspaceId && formId),
    staleTime: Number.POSITIVE_INFINITY,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchOnMount: false,
    retry: false,
    meta: { suppressErrorToast: true },
  })
}

/** Submit a form. The caller renders failures. */
export function useSubmitForm({ workspaceId, formId }: FormScope) {
  const queryClient = useQueryClient()
  return useMutation<FormSubmissionReceipt, ApiError, FormSubmissionCreate>({
    mutationFn: (requestBody) =>
      formsSubmitForm({ workspaceId, formId, requestBody }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: formKeys.mySubmissions })
      queryClient.invalidateQueries({
        queryKey: formKeys.submissions(workspaceId, formId),
      })
      queryClient.invalidateQueries({ queryKey: formKeys.list(workspaceId) })
    },
    meta: { suppressErrorToast: true },
  })
}

/** Create the form of a workflow. */
export function useCreateForm(workspaceId: string) {
  const queryClient = useQueryClient()
  return useMutation<FormRead, ApiError, FormCreate>({
    mutationFn: (requestBody) => formsCreateForm({ workspaceId, requestBody }),
    onSuccess: (form) => {
      queryClient.setQueryData(formKeys.detail(workspaceId, form.id), form)
      queryClient.invalidateQueries({ queryKey: formKeys.list(workspaceId) })
    },
    meta: { suppressErrorToast: true },
  })
}

/** Update a form's status, config or both. The config is replaced whole. */
export function useUpdateForm({ workspaceId, formId }: FormScope) {
  const queryClient = useQueryClient()
  return useMutation<FormRead, ApiError, FormUpdate>({
    mutationFn: (requestBody) =>
      formsUpdateForm({ workspaceId, formId, requestBody }),
    onSuccess: (form) => {
      queryClient.setQueryData(formKeys.detail(workspaceId, formId), form)
      queryClient.invalidateQueries({ queryKey: formKeys.list(workspaceId) })
    },
    meta: { suppressErrorToast: true },
  })
}

/** Delete a form with its submissions. */
export function useDeleteForm(workspaceId: string) {
  const queryClient = useQueryClient()
  return useMutation<void, ApiError, string>({
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
}

/** A form's submissions with answers, submitter and run status. */
export function useFormSubmissions(
  { workspaceId, formId }: FormScope,
  options: { enabled?: boolean } = {}
) {
  return useCursorList(
    formKeys.submissions(workspaceId, formId),
    (cursor) =>
      formsListFormSubmissions({
        workspaceId,
        formId,
        limit: PAGE_SIZE,
        cursor,
      }),
    options
  )
}

/** Search organization members for a form's `user` field. */
export function useFormUserSearch(
  { workspaceId, formId }: FormScope,
  search: string,
  { enabled = true }: { enabled?: boolean } = {}
) {
  const term = search.trim()
  const { items, isLoading, ...rest } = useCursorList(
    ["form-users", workspaceId, formId, term],
    (cursor) =>
      formsSearchFormUsers({
        workspaceId,
        formId,
        search: term || undefined,
        limit: USER_SEARCH_PAGE_SIZE,
        cursor,
      }),
    { enabled: enabled && Boolean(workspaceId && formId), retry: false }
  )
  return { users: items, usersIsLoading: isLoading, ...rest }
}

/** Online forms across every workspace the requester can read forms in. */
export function useRequesterForms() {
  return useCursorList(["requester-forms"], (cursor) =>
    formsListIntakeForms({ limit: PAGE_SIZE, cursor })
  )
}

/** The requester's own submissions across workspaces, without run fields. */
export function useRequesterSubmissions() {
  return useCursorList(formKeys.mySubmissions, (cursor) =>
    formsListIntakeSubmissions({ limit: PAGE_SIZE, cursor })
  )
}

/** Find the workspace a form lives in, for a requester with only its ID. */
export function useFormLocation(formId: string) {
  return useQuery<FormIntakeLocation, ApiError>({
    queryKey: ["requester-form", formId],
    queryFn: () => formsLocateIntakeForm({ formId }),
    enabled: Boolean(formId),
    retry: false,
    meta: { suppressErrorToast: true },
  })
}
