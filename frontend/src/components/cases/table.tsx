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
import { DataTable, type DataTableToolbarProps } from "@/components/table"

export default function CaseTable() {
  return (
    <CasePanelProvider className="sm:w-3/5 sm:max-w-none md:w-3/5 lg:w-3/5">
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
  const { cases, setCases, commitCases } = useCasesContext()
  const { setPanelCase: setSidePanelCase, setIsOpen } = useCasePanelContext()
  const [isProcessing, setIsProcessing] = useState(false)
  const [isCommitable, setIsCommitable] = useState(false)
  const [isCommitting, setIsCommitting] = useState(false)
  const session = useSession()

  const memoizedColumns = useMemo(() => columns, [columns])

  const commitChanges = async () => {
    setIsCommitting(() => true)
    try {
      commitCases()
    } catch (error) {
      console.error("Error committing changes:", error)
      setIsProcessing(() => false)
    } finally {
      setIsCommitting(() => false)
    }
  }

  const fetchCompletions = async () => {
    // 1. Set all 'null' values to a loading icon
    setIsProcessing(() => true)

    const generator = streamGenerator("/completions/cases/stream", session, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(cases),
    })

    try {
      for await (const response of generator) {
        const parsedJSONResponse = JSON.parse(response)
        const parsedResponse =
          caseCompletionUpdateSchema.parse(parsedJSONResponse)
        const id = parsedResponse.id
        const prevCase = cases.find((c) => c.id === id)
        if (!prevCase) {
          console.error(`Case ${id} not found`)
          continue
        }
        const { action, context } = parsedResponse.response
        const updatedCase = {
          ...prevCase,
          // Replace if the originals are null
          action: prevCase?.action ?? action,
          context: prevCase?.context ?? context,
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
      setIsProcessing(() => false)
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
          <Button
            onClick={commitChanges}
            className="mt-1 text-xs"
            disabled={isProcessing || !isCommitable}
          >
            {!isCommitting ? (
              <GitGraph className="mr-2 h-3 w-3  fill-secondary" />
            ) : (
              <Loader2 className="stroke-6 mr-2 h-4 w-4 animate-spin transition-all ease-in-out" />
            )}
            Commit
          </Button>
          <Button
            onClick={fetchCompletions}
            className="mt-1 text-xs"
            disabled={isProcessing}
          >
            {!isProcessing ? (
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
        isProcessing={isProcessing}
      />
    </div>
  )
}
