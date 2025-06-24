import type {
  RegistryActionTemplateImpl_Output,
  RegistryActionUDFImpl,
} from "@/client"

export function isTemplateAction(
  implementation?: RegistryActionTemplateImpl_Output | RegistryActionUDFImpl
): implementation is RegistryActionTemplateImpl_Output {
  return implementation?.type === "template"
}
