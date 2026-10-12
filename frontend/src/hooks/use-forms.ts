import {
  type ApiError,
  type FormResolved,
  type FormSubmissionCreate,
  type FormSubmissionReceipt,
  type FormsSearchFormUsersResponse,
  type FormUserRead,
  formsRenderForm,
  formsSearchFormUsers,
  formsSubmitForm,
} from "@/client"
import { requesterFormKeys } from "@/hooks/use-requester-forms"
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@/lib/query"

const USER_SEARCH_PAGE_SIZE = 20
const EMPTY_USERS: readonly FormUserRead[] = []

/** Query keys of the workspace-scoped form queries. */
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
 * Search organization members for a form's `user` field, a page at a time.
 *
 * The server returns 404 when the saved form has no `user` field, which
 * happens for an unsaved preview. That reads as no results.
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
