import YAML, {
  type DocumentOptions,
  type ParseOptions,
  type ScalarTag,
  type SchemaOptions,
  type Tags,
  type ToJSOptions,
  type ToStringOptions,
} from "yaml"

const YAML_BOOL_TAG = "tag:yaml.org,2002:bool"
const YAML_TIMESTAMP_TAG = "tag:yaml.org,2002:timestamp"
const PYYAML_TRUE = /^(?:[Yy]es|YES|[Tt]rue|TRUE|[Oo]n|ON)$/
const PYYAML_FALSE = /^(?:[Nn]o|NO|[Ff]alse|FALSE|[Oo]ff|OFF)$/

/**
 * The backend parses action inputs with PyYAML, which implements YAML 1.1:
 * plain `on`/`off`/`yes`/`no` are booleans. `yaml` defaults to YAML 1.2, so it
 * reads `"on"` as a string but writes it back unquoted, which PyYAML then
 * coerces to a boolean. Match PyYAML's SafeLoader instead: YAML 1.1 without
 * `y`/`n` booleans, and without timestamps (the backend keeps dates as strings).
 */
const pyyamlTags = (tags: Tags): Tags => {
  const result: Tags = []
  for (const tag of tags) {
    if (typeof tag !== "string" && tag.tag === YAML_TIMESTAMP_TAG) {
      continue
    }
    if (typeof tag !== "string" && tag.tag === YAML_BOOL_TAG && tag.identify) {
      const test = tag.identify(true) ? PYYAML_TRUE : PYYAML_FALSE
      result.push({ ...(tag as ScalarTag), test })
      continue
    }
    result.push(tag)
  }
  return result
}

export const PYYAML_COMPAT_OPTIONS = {
  version: "1.1",
  customTags: pyyamlTags,
} satisfies DocumentOptions & SchemaOptions

export function parseYaml(
  src: string,
  options?: ParseOptions & DocumentOptions & SchemaOptions & ToJSOptions
): ReturnType<typeof YAML.parse> {
  return YAML.parse(src, { ...options, ...PYYAML_COMPAT_OPTIONS })
}

export function stringifyYaml(
  value: unknown,
  options?: DocumentOptions & SchemaOptions & ParseOptions & ToStringOptions
): string {
  return YAML.stringify(value, { ...options, ...PYYAML_COMPAT_OPTIONS })
}
