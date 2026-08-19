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
 * A hook-level `onError` owns user-facing feedback by default. Set
 * `meta.suppressErrorToast` to `false` for cleanup-only callbacks that should
 * also receive shared feedback, or to `true` when a `mutateAsync` caller owns
 * feedback outside the hook.
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
  const hasLocalErrorHandler = options.onError !== undefined
  const suppressErrorToast =
    options.meta?.suppressErrorToast === true ||
    (hasLocalErrorHandler && options.meta?.suppressErrorToast !== false)

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
