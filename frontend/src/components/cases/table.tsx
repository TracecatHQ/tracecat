"use client"

import { useMemo, useState } from "react"
import CasePanelProvider, { useCasePanelContext } from "@/providers/case-panel"
import { useCasesContext } from "@/providers/cases"
import { useSession } from "@/providers/session"
import { type Row } from "@tanstack/react-table"
import { GitGraph, Loader2, Sparkles } from "lucide-react"

import {
  caseCompletionUpdateSchema,
  caseSchema,
  type Case,
} from "@/types/schemas"
import { streamGenerator } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { tableHeaderAuxOptions } from "@/components/cases/aux-click-menu-config"
import { columns } from "@/components/cases/columns"
import {
  indicators,
  priorities,
  statuses,
} from "@/components/cases/data/categories"
import { ConfirmationDialog } from "@/components/confirmation-dialog"
import { DataTable, type DataTableToolbarProps } from "@/components/table"

export default function CaseTable() {
  return (
    <CasePanelProvider className="h-full overflow-auto sm:w-3/5 sm:max-w-none md:w-3/5 lg:w-4/5 lg:max-w-[1200px]">
      <InternalCaseTable />
    </CasePanelProvider>
  )
}
const defaultToolbarProps: DataTableToolbarProps = {
  filterProps: {
    placeholder: "Filter cases...",
    column: "title",
  },
  fields: [
    {
      column: "status",
      title: "Status",
      options: statuses,
    },
    {
      column: "priority",
      title: "Priority",
      options: priorities,
    },
    {
      column: "malice",
      title: "Malice",
      options: indicators,
    },
  ],
}
/**
 *
 * Steps to replace:
 * 1. Set all 'null' values to a spinner icon
 * 2. Perform the fetch stream operation
 * 3. Replace all the nulls with the actual values according to the case ID
 * 2.
 */
function InternalCaseTable() {
  const {
    cases,
    setCases,
    commitCases,
    isCommitable,
    setIsCommitable,
    isCommitting,
  } = useCasesContext()
  const { setPanelCase: setSidePanelCase, setIsOpen } = useCasePanelContext()
  const [isAutocompleting, setIsAutocompleting] = useState(false)
  const session = useSession()

  const memoizedColumns = useMemo(() => columns, [columns])

  /**
   * Perform autocompletions for the cases
   *
   * - Set all completable 'null' values to a loading icon
   * - We only support autocompleting `tags` for now
   */
  const fetchCompletions = async () => {
    // 1. Set all 'null' values to a loading icon
    setIsAutocompleting(() => true)

    const generator = streamGenerator("/completions/cases/stream", session, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        cases,
        fields: ["tags"],
      }),
    })

    try {
      for await (const response of generator) {
        const parsedJSONResponse = JSON.parse(response)
        console.log("Parsed JSON Response", parsedJSONResponse)
        const parsedResponse =
          caseCompletionUpdateSchema.parse(parsedJSONResponse)
        const id = parsedResponse.id
        const prevCase = cases.find((c) => c.id === id)
        if (!prevCase) {
          console.error(`Case ${id} not found`)
          continue
        }
        const { tags } = parsedResponse.response
        const updatedCase = {
          ...prevCase,
          // Add the new tags to the existing tags, without overwriting
          tags: prevCase.tags.concat(
            tags.map((tag) => ({ ...tag, is_ai_generated: true }))
          ),
        }
        console.log("Updated Case", updatedCase.id)
        const newCase = caseSchema.parse(updatedCase)
        // Set the new case in the cases array
        setCases((cases) => cases.map((c) => (c.id === id ? newCase : c)))
        setIsCommitable(() => true)
      }
    } catch (error) {
      console.error("Error reading stream:", error)
    } finally {
      setIsAutocompleting(() => false)
    }
  }

  function handleClickRow(row: Row<Case>) {
    return () => {
      setSidePanelCase(row.original)
      setIsOpen(true)
    }
  }
  return (
    <div className="w-full space-y-4">
      <div className="flex items-end">
        <div className="items-start space-y-2 text-left">
          <h2 className="text-2xl font-bold tracking-tight">Cases</h2>
          <p className="text-md text-muted-foreground">
            Here are the cases for this workflow.
          </p>
        </div>
        <div className="flex w-full flex-1 justify-end space-x-2">
          <ConfirmationDialog
            title={"Commit changes?"}
            description="Are you sure you want to commit the AI autocomplete changes? This action will overwrite the cases and cannot be undone."
            onConfirm={commitCases}
          >
            <Button
              className="mt-1 text-xs"
              disabled={isAutocompleting || !isCommitable}
            >
              {!isCommitting ? (
                <GitGraph className="mr-2 h-3 w-3  fill-secondary" />
              ) : (
                <Loader2 className="stroke-6 mr-2 h-4 w-4 animate-spin transition-all ease-in-out" />
              )}
              Commit
            </Button>
          </ConfirmationDialog>
          <Button
            onClick={fetchCompletions}
            className="mt-1 text-xs"
            disabled={isAutocompleting}
          >
            {!isAutocompleting ? (
              <Sparkles className="mr-2 h-3 w-3  fill-secondary" />
            ) : (
              <Loader2 className="stroke-6 mr-2 h-4 w-4 animate-spin transition-all ease-in-out" />
            )}
            Autocomplete
          </Button>
        </div>
      </div>
      <DataTable
        data={cases}
        columns={memoizedColumns}
        onClickRow={handleClickRow}
        toolbarProps={defaultToolbarProps}
        tableHeaderAuxOptions={tableHeaderAuxOptions}
        isProcessing={isAutocompleting}
      />
    </div>
  )
}
