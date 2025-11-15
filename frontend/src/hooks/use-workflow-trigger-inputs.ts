"use client"

import { useMemo } from "react"
import type { CaseRead } from "@/client"
import { useLocalStorage } from "@/hooks/use-local-storage"

export function useWorkflowTriggerInputs(caseData: CaseRead) {
  const [groupCaseFields, setGroupCaseFields] = useLocalStorage(
    "groupCaseFields",
    false
  )

  const caseFieldsRecord = useMemo(
    () =>
      Object.fromEntries(
        caseData.fields
          .filter((field) => !field.reserved)
          .map((field) => [field.id, field.value])
      ),
    [caseData.fields]
  )

  const fallbackInputs = useMemo(() => {
    if (groupCaseFields) {
      return {
        case_id: caseData.id,
        case_fields: caseFieldsRecord,
      }
    }
    return {
      case_id: caseData.id,
      ...caseFieldsRecord,
    }
  }, [caseData.id, caseFieldsRecord, groupCaseFields])

  return {
    caseFieldsRecord,
    fallbackInputs,
    groupCaseFields,
    setGroupCaseFields,
  }
}
