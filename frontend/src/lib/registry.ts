import type {
  RegistryActionTemplateImpl,
  RegistryActionUDFImpl,
} from "@/client"

export function isTemplateAction(
  implementation?: RegistryActionTemplateImpl | RegistryActionUDFImpl
): implementation is RegistryActionTemplateImpl {
  return implementation?.type === "template"
}
