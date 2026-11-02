import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { type FormPreview, type FormRead, formsValidateForm } from "@/client"
import { useDebounce } from "@/hooks/use-debounce"
import { type FormScope, formKeys } from "@/hooks/use-forms"
import { getFormConfigIssues } from "@/lib/forms/errors"
import {
  type FormConfigProblem,
  locateFormIssues,
  parseFormConfigYaml,
  serializeFormConfig,
} from "@/lib/forms/yaml-diagnostics"
import { useQuery } from "@/lib/query"

/** How long the editor has to be idle before its text is checked. */
const FORM_CONFIG_CHECK_DELAY_MS = 500

/** The config editor's YAML buffer and how it relates to the saved form. */
export interface FormConfigDraft {
  text: string
  setText: (text: string) => void
  /** Whether the buffer differs from the config it started from. */
  isDirty: boolean
  /** The `config_version` of the config the buffer started from. */
  baseVersion: string
  /** Whether the saved config changed elsewhere under unsaved edits. */
  hasRemoteChange: boolean
  /** Whether the unsaved edits come from an earlier visit in this tab. */
  isRestored: boolean
  /** Drop the buffer and take the saved config. */
  reload: () => void
  /**
   * Take the form this editor just saved, from the `submitted` text, as the
   * new starting point. Edits made since `submitted` was sent are kept.
   */
  markSaved: (saved: FormRead, submitted: string) => void
}

interface StoredDraft {
  text: string
  baseText: string
  baseVersion: string
}

const draftStorageKey = (formId: string) =>
  `tracecat:form-config-draft:${formId}`

// Storage can be unavailable (private mode, blocked site data, quota). The
// editor works without it; only the restore after navigation is lost.
function readStoredDraft(formId: string): StoredDraft | null {
  try {
    const raw = sessionStorage.getItem(draftStorageKey(formId))
    const stored = (raw ? JSON.parse(raw) : null) as Partial<StoredDraft> | null
    const { text, baseText, baseVersion } = stored ?? {}
    if (
      typeof text === "string" &&
      typeof baseText === "string" &&
      typeof baseVersion === "string" &&
      text !== baseText
    ) {
      return { text, baseText, baseVersion }
    }
  } catch {}
  return null
}

function writeStoredDraft(formId: string, draft: StoredDraft | null): void {
  try {
    if (draft) {
      sessionStorage.setItem(draftStorageKey(formId), JSON.stringify(draft))
    } else {
      sessionStorage.removeItem(draftStorageKey(formId))
    }
  } catch {}
}

/**
 * Hold the config editor's YAML for a form, serialized from the saved JSON
 * config. When the saved config changes (another tab, the chat, a git
 * pull), a clean buffer follows it and a dirty one is left alone and flagged.
 *
 * Reloading or closing the tab with unsaved edits asks for confirmation.
 * In-app navigation does not: the buffer is kept in `sessionStorage` per
 * form and restored, flagged, when the form is opened again in the same tab.
 */
export function useFormConfigDraft(
  form: FormRead,
  /** Starting text shown, unsaved and clean, while the saved config is empty. */
  scaffold = ""
): FormConfigDraft {
  const savedText = useMemo(
    () => serializeFormConfig(form.config),
    [form.config]
  )
  const serverText = savedText || scaffold
  const [stored] = useState(() => readStoredDraft(form.id))
  const [text, setText] = useState(stored?.text ?? serverText)
  // The serialized config the buffer was last in sync with. A restored draft
  // keeps the base it was written against, so a config that changed since
  // then shows as a remote change.
  const [baseText, setBaseText] = useState(stored?.baseText ?? serverText)
  const [baseVersion, setBaseVersion] = useState(
    stored?.baseVersion ?? form.config_version
  )
  const [isRestored, setIsRestored] = useState(stored !== null)
  const isDirty = text !== baseText
  const isBehind =
    serverText !== baseText || form.config_version !== baseVersion

  const adopt = useCallback((next: string, version: string) => {
    setText(next)
    setBaseText(next)
    setBaseVersion(version)
    setIsRestored(false)
  }, [])

  useEffect(() => {
    if (isBehind && !isDirty) {
      adopt(serverText, form.config_version)
    }
  }, [isBehind, isDirty, serverText, form.config_version, adopt])

  useEffect(() => {
    writeStoredDraft(form.id, isDirty ? { text, baseText, baseVersion } : null)
  }, [form.id, isDirty, text, baseText, baseVersion])

  useEffect(() => {
    if (!isDirty) {
      return
    }
    function warn(event: BeforeUnloadEvent) {
      event.preventDefault()
    }
    window.addEventListener("beforeunload", warn)
    return () => window.removeEventListener("beforeunload", warn)
  }, [isDirty])

  return {
    text,
    setText,
    isDirty,
    baseVersion,
    // A scaffold that loads under edits to an empty config is not a change.
    hasRemoteChange:
      isDirty &&
      (form.config_version !== baseVersion ||
        (savedText !== baseText &&
          !(savedText === "" && (baseText === "" || baseText === scaffold)))),
    isRestored: isRestored && isDirty,
    reload: () => adopt(serverText, form.config_version),
    markSaved: (saved, submitted) => {
      const savedText = serializeFormConfig(saved.config)
      // Text typed while the save was in flight stays, unsaved, on the new base.
      setText((current) => (current === submitted ? savedText : current))
      setBaseText(savedText)
      setBaseVersion(saved.config_version)
      setIsRestored(false)
    },
  }
}

/** What checking the editor's text against the server has found. */
export interface FormConfigCheck {
  /** The last preview of a config with no errors. Kept while the text is invalid. */
  preview: FormPreview | null
  /** Problems of the last text that was checked. */
  problems: FormConfigProblem[]
  /** Whether the last text that was checked has errors. Warnings do not count. */
  hasErrors: boolean
  /** Set when the check itself failed, for example offline. Not a config error. */
  checkFailed: boolean
}

/**
 * Check the config editor's text and keep a preview of it: the one preview
 * of a form in the builder, for editors and read-only builders alike.
 *
 * Idle text is parsed here and, when it is valid YAML, resolved by the
 * server against the form's workflow inputs. The config is a request body,
 * never a query key: it can hold a megabyte of inline images. Only the
 * latest answer is applied, and a config with errors keeps the last preview.
 */
export function useFormConfigCheck(
  { workspaceId, formId }: FormScope,
  text: string
): FormConfigCheck {
  const [debouncedText] = useDebounce(text, FORM_CONFIG_CHECK_DELAY_MS)
  const sequence = useRef(0)
  const [state, setState] = useState<Omit<FormConfigCheck, "hasErrors">>({
    preview: null,
    problems: [],
    checkFailed: false,
  })

  useEffect(() => {
    const current = ++sequence.current
    const parsed = parseFormConfigYaml(debouncedText)
    if (!parsed.config) {
      setState((previous) => ({
        ...previous,
        problems: parsed.problems,
        checkFailed: false,
      }))
      return
    }
    formsValidateForm({
      workspaceId,
      formId,
      requestBody: { config: parsed.config },
    })
      .then((result) => {
        if (current !== sequence.current) {
          return
        }
        setState((previous) => ({
          preview: result.valid ? result.preview : previous.preview,
          problems: locateFormIssues(debouncedText, result.issues),
          checkFailed: false,
        }))
      })
      .catch((error: unknown) => {
        if (current !== sequence.current) {
          return
        }
        const issues = getFormConfigIssues(error)
        setState((previous) => ({
          ...previous,
          problems: issues ? locateFormIssues(debouncedText, issues) : [],
          checkFailed: issues === null,
        }))
      })
  }, [debouncedText, workspaceId, formId])

  return {
    ...state,
    hasErrors: state.problems.some((problem) => problem.severity === "error"),
  }
}

/** The editor scaffold for an empty config and the inputs a form can show. */
export interface FormConfigScaffold {
  /** Every input the empty config renders, as an editable layout. */
  text: string
  /** Names of the inputs the empty config renders, in schema order. */
  fields: string[]
}

const EMPTY_SCAFFOLD: FormConfigScaffold = { text: "", fields: [] }

export function scaffoldFromPreview(
  preview: FormPreview | null | undefined
): FormConfigScaffold {
  const fields = (preview?.blocks ?? []).flatMap((block) =>
    block.type === "field" ? [block.name] : []
  )
  if (fields.length === 0) {
    return EMPTY_SCAFFOLD
  }
  const text = [
    "# Generated from the workflow inputs. Edit and save to customize.",
    "# Title and description default to the workflow's.",
    "layout:",
    ...fields.map((name) => `  - field: ${name}`),
    "",
  ].join("\n")
  return { text, fields }
}

/**
 * Resolve an empty config against the form's workflow inputs, so the editor
 * can show what the form renders by default instead of a blank buffer, and
 * the slash menu can offer each input as a field block.
 */
export function useFormConfigScaffold(form: FormRead): FormConfigScaffold {
  const { data } = useQuery<FormPreview | null>({
    queryKey: [
      ...formKeys.detail(form.workspace_id, form.id),
      "scaffold",
      form.workflow_version ?? null,
    ],
    queryFn: async () => {
      const result = await formsValidateForm({
        workspaceId: form.workspace_id,
        formId: form.id,
        requestBody: { config: {} },
      })
      return result.preview ?? null
    },
    retry: false,
    staleTime: Number.POSITIVE_INFINITY,
    meta: { suppressErrorToast: true },
  })
  return useMemo(() => scaffoldFromPreview(data), [data])
}
