import type {
  RegistryActionTemplateImpl,
  RegistryActionUDFImpl,
} from "@/client"

export function isTemplateAction(
  implementation?: RegistryActionTemplateImpl | RegistryActionUDFImpl
): implementation is RegistryActionTemplateImpl {
  return implementation?.type === "template"
}

/**
 * Humanize a registry action leaf name or dotted id into a readable title.
 *
 * Accepts either a leaf name (`list_tools`) or a full dotted action id
 * (`tools.okta.list_tools`); the last segment is used, with underscores
 * replaced by spaces and the first letter capitalized, e.g. `List tools`.
 */
export function humanizeActionName(nameOrAction: string): string {
  const leaf = nameOrAction.split(".").pop() ?? nameOrAction
  const spaced = leaf.replace(/_/g, " ")
  return spaced.charAt(0).toUpperCase() + spaced.slice(1)
}
