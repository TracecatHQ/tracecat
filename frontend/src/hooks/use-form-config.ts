import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import type { FormConfig_Output, FormPreview, FormRead } from "@/client"
import { useDebounce } from "@/hooks/use-debounce"
import { useValidateFormConfig } from "@/hooks/use-forms"
import { getFormConfigIssues } from "@/lib/forms/errors"
import {
  type FormConfigProblem,
  locateFormIssues,
  parseFormConfigYaml,
  serializeFormConfig,
} from "@/lib/forms/yaml-diagnostics"

/** How long the editor has to be idle before its text is checked. */
export const FORM_CONFIG_CHECK_DELAY_MS = 500

/** The config editor's buffer and how it relates to the saved form. */
export interface FormConfigDraft {
  /** The YAML in the editor. */
  text: string
  setText: (text: string) => void
  /** Whether the buffer differs from the config it started from. */
  isDirty: boolean
  /**
   * Whether the saved config changed (another tab, the chat, a git pull)
   * while the buffer holds unsaved edits.
   */
  hasRemoteChange: boolean
  /**
   * Whether the buffer holds unsaved edits from an earlier visit to this
   * form in the same browser tab.
   */
  isRestored: boolean
  /** Drop the buffer and take the saved config. */
  reload: () => void
  /** Take a config this editor just saved as the new starting point. */
  markSaved: (config: FormConfig_Output) => void
}

interface StoredDraft {
  text: string
  baseText: string
}

function draftStorageKey(formId: string): string {
  return `tracecat:form-config-draft:${formId}`
}

// Storage can be unavailable (private mode, blocked site data, quota). The
// editor works without it; only the restore after navigation is lost.
function readStoredDraft(formId: string): StoredDraft | null {
  try {
    const raw = sessionStorage.getItem(draftStorageKey(formId))
    const stored: unknown = raw ? JSON.parse(raw) : null
    if (
      typeof stored === "object" &&
      stored !== null &&
      "text" in stored &&
      "baseText" in stored &&
      typeof stored.text === "string" &&
      typeof stored.baseText === "string" &&
      stored.text !== stored.baseText
    ) {
      return { text: stored.text, baseText: stored.baseText }
    }
  } catch {
    // Fall through: no draft.
  }
  return null
}

function writeStoredDraft(formId: string, draft: StoredDraft | null): void {
  try {
    if (draft) {
      sessionStorage.setItem(draftStorageKey(formId), JSON.stringify(draft))
    } else {
      sessionStorage.removeItem(draftStorageKey(formId))
    }
  } catch {
    // Nothing to do: the draft just won't survive navigation.
  }
}

/**
 * Hold the config editor's YAML for a form.
 *
 * The server stores JSON, so the buffer starts from a serialization of the
 * saved config and is re-serialized after every save: comments do not
 * survive. When the saved config changes underneath the editor, a clean
 * buffer follows it and a dirty one is left alone and flagged.
 *
 * Unsaved edits are guarded two ways. Reloading or closing the tab asks for
 * confirmation. Navigating inside the app does not ask: the unsaved buffer
 * is kept in `sessionStorage` per form and put back, flagged `isRestored`,
 * when the form is opened again in the same tab. Saving or `reload` drops it.
 */
export function useFormConfigDraft(form: FormRead): FormConfigDraft {
  const serverText = useMemo(
    () => serializeFormConfig(form.config),
    [form.config]
  )
  const [stored] = useState(() => readStoredDraft(form.id))
  const [text, setText] = useState(stored?.text ?? serverText)
  // The serialized config the buffer was last in sync with. A restored draft
  // keeps the base it was written against, so a config that changed since
  // then shows as a remote change.
  const [baseText, setBaseText] = useState(stored?.baseText ?? serverText)
  const [isRestored, setIsRestored] = useState(stored !== null)
  const isDirty = text !== baseText

  useEffect(() => {
    if (serverText !== baseText && !isDirty) {
      setText(serverText)
      setBaseText(serverText)
    }
  }, [serverText, baseText, isDirty])

  useEffect(() => {
    writeStoredDraft(form.id, isDirty ? { text, baseText } : null)
  }, [form.id, isDirty, text, baseText])

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

  const reload = useCallback(() => {
    setText(serverText)
    setBaseText(serverText)
    setIsRestored(false)
  }, [serverText])

  const markSaved = useCallback((config: FormConfig_Output) => {
    const savedText = serializeFormConfig(config)
    setText(savedText)
    setBaseText(savedText)
    setIsRestored(false)
  }, [])

  return {
    text,
    setText,
    isDirty,
    hasRemoteChange: isDirty && serverText !== baseText,
    isRestored: isRestored && isDirty,
    reload,
    markSaved,
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
  /** Whether the text on screen is still waiting to be checked. */
  isChecking: boolean
  /** Set when the check itself failed, for example offline. Not a config error. */
  checkFailed: boolean
}

interface CheckState {
  text: string | null
  preview: FormPreview | null
  problems: FormConfigProblem[]
  checkFailed: boolean
}

const INITIAL_CHECK: CheckState = {
  text: null,
  preview: null,
  problems: [],
  checkFailed: false,
}

/**
 * Check the config editor's text and keep a preview of it.
 *
 * Once the text has been idle for {@link FORM_CONFIG_CHECK_DELAY_MS}, it is
 * parsed here and, when it is valid YAML, resolved by the server against the
 * workflow's inputs. Answers can arrive out of order, so each request takes
 * a sequence number and only the latest one is applied. A config with errors
 * updates the problems and leaves the previous preview in place.
 *
 * Needs `form:update`; pass `enabled: false` for a read-only viewer.
 */
export function useFormConfigCheck({
  workspaceId,
  workflowId,
  text,
  enabled,
}: {
  workspaceId: string
  workflowId: string
  text: string
  enabled: boolean
}): FormConfigCheck {
  const [debouncedText] = useDebounce(text, FORM_CONFIG_CHECK_DELAY_MS)
  const { validateFormConfig } = useValidateFormConfig(workspaceId)
  const validateRef = useRef(validateFormConfig)
  validateRef.current = validateFormConfig
  const sequence = useRef(0)
  const [state, setState] = useState<CheckState>(INITIAL_CHECK)

  useEffect(() => {
    if (!enabled) {
      return
    }
    const current = ++sequence.current
    const checked = debouncedText
    const parsed = parseFormConfigYaml(checked)
    if (!parsed.config) {
      setState((previous) => ({
        ...previous,
        text: checked,
        problems: parsed.problems,
        checkFailed: false,
      }))
      return
    }
    validateRef
      .current({ workflow_id: workflowId, config: parsed.config })
      .then((result) => {
        if (current !== sequence.current) {
          return
        }
        setState((previous) => ({
          text: checked,
          preview: result.valid ? result.preview : previous.preview,
          problems: locateFormIssues(checked, result.issues),
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
          text: checked,
          problems: issues ? locateFormIssues(checked, issues) : [],
          checkFailed: issues === null,
        }))
      })
  }, [debouncedText, enabled, workflowId])

  return {
    preview: state.preview,
    problems: state.problems,
    hasErrors: state.problems.some((problem) => problem.severity === "error"),
    isChecking: enabled && state.text !== text,
    checkFailed: state.checkFailed,
  }
}
