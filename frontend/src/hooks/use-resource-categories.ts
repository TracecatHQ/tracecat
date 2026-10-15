import { useMemo } from "react"
import { useFormsAccess } from "@/hooks/use-forms-access"
import { RESOURCE_CATEGORIES } from "@/lib/rbac"

/**
 * The RBAC resource categories a role or service-account editor shows.
 *
 * Same as `RESOURCE_CATEGORIES`, without Forms while the `forms` feature flag
 * is off. Scopes already granted in a hidden category are left as they are.
 */
export function useResourceCategories(): typeof RESOURCE_CATEGORIES {
  const formsHidden = useFormsAccess() === "hidden"
  return useMemo(() => {
    if (!formsHidden) {
      return RESOURCE_CATEGORIES
    }
    return Object.fromEntries(
      Object.entries(RESOURCE_CATEGORIES).filter(([key]) => key !== "forms")
    )
  }, [formsHidden])
}
