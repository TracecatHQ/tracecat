import {
  type ApiError,
  type FormIntakeLocation,
  type FormIntakeRead,
  type FormIntakeSubmissionRead,
  type FormsListIntakeFormsResponse,
  type FormsListIntakeSubmissionsResponse,
  formsListIntakeForms,
  formsListIntakeSubmissions,
  formsLocateIntakeForm,
} from "@/client"
import { useInfiniteQuery, useQuery } from "@/lib/query"

const PAGE_SIZE = 50
const EMPTY_FORMS: readonly FormIntakeRead[] = []
const EMPTY_SUBMISSIONS: readonly FormIntakeSubmissionRead[] = []

/** Query keys of the requester (intake) queries. */
export const requesterFormKeys = {
  forms: ["requester-forms"] as const,
  form: (formId: string) => ["requester-form", formId] as const,
  submissions: ["requester-submissions"] as const,
}

/**
 * Online forms across every workspace the requester can read forms in, a
 * page at a time.
 */
export function useRequesterForms(options: { enabled?: boolean } = {}) {
  const query = useInfiniteQuery<
    FormsListIntakeFormsResponse,
    ApiError,
    { pages: FormsListIntakeFormsResponse[] },
    readonly unknown[],
    string | null
  >({
    queryKey: requesterFormKeys.forms,
    queryFn: ({ pageParam }) =>
      formsListIntakeForms({ limit: PAGE_SIZE, cursor: pageParam }),
    initialPageParam: null,
    getNextPageParam: (lastPage) =>
      lastPage.has_more ? (lastPage.next_cursor ?? undefined) : undefined,
    enabled: options.enabled ?? true,
    meta: { suppressErrorToast: true },
  })
  return {
    forms: query.data?.pages.flatMap((page) => page.items) ?? EMPTY_FORMS,
    formsIsLoading: query.isLoading,
    formsError: query.error,
    hasNextPage: query.hasNextPage,
    isFetchingNextPage: query.isFetchingNextPage,
    fetchNextPage: query.fetchNextPage,
    refetch: query.refetch,
  }
}

/**
 * Find the workspace a form lives in, for a requester who only has its ID.
 *
 * Fails with 404 for an unknown form and for one the requester cannot read.
 */
export function useRequesterForm(formId: string) {
  const query = useQuery<FormIntakeLocation, ApiError>({
    queryKey: requesterFormKeys.form(formId),
    queryFn: () => formsLocateIntakeForm({ formId }),
    enabled: Boolean(formId),
    retry: false,
    meta: { suppressErrorToast: true },
  })
  return {
    location: query.data,
    locationIsLoading: query.isLoading,
    locationError: query.error,
    refetch: query.refetch,
  }
}

/**
 * The requester's own submissions across workspaces, newest first, a page at
 * a time. Rows carry no workflow run fields.
 */
export function useRequesterSubmissions(options: { enabled?: boolean } = {}) {
  const query = useInfiniteQuery<
    FormsListIntakeSubmissionsResponse,
    ApiError,
    { pages: FormsListIntakeSubmissionsResponse[] },
    readonly unknown[],
    string | null
  >({
    queryKey: requesterFormKeys.submissions,
    queryFn: ({ pageParam }) =>
      formsListIntakeSubmissions({ limit: PAGE_SIZE, cursor: pageParam }),
    initialPageParam: null,
    getNextPageParam: (lastPage) =>
      lastPage.has_more ? (lastPage.next_cursor ?? undefined) : undefined,
    enabled: options.enabled ?? true,
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
