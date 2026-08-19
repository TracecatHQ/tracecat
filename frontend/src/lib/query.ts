import {
  type DefaultError,
  type QueryClient,
  useMutation as tanstackUseMutation,
  type UseMutationOptions,
} from "@tanstack/react-query"
import { chainError } from "@/lib/errors"

export * from "@tanstack/react-query"

/**
 * Run a React Query mutation through Tracecat's shared error pipeline.
 *
 * This intentionally mirrors React Query's public signature so existing call
 * sites only need to change their import path while the facade remains in use.
 * Pass error handlers through this hook's options; per-call `onError`
 * callbacks supplied to `mutate` or `mutateAsync` bypass this composition.
 * Set `meta.suppressErrorToast` when a caller catches `mutateAsync` and owns
 * all user-facing error feedback; hook-level `onError` callbacks still run.
 */
export function useMutation<
  TData = unknown,
  TError = DefaultError,
  TVariables = void,
  TOnMutateResult = unknown,
>(
  options: UseMutationOptions<TData, TError, TVariables, TOnMutateResult>,
  queryClient?: QueryClient
) {
  const suppressErrorToast = options.meta?.suppressErrorToast === true

  return tanstackUseMutation(
    {
      ...options,
      onError: suppressErrorToast
        ? options.onError
        : chainError(options.onError),
    },
    queryClient
  )
}
