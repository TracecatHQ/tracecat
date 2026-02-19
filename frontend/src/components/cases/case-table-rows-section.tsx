"use client"

import { useState } from "react"
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"
import { Skeleton } from "@/components/ui/skeleton"
import { useCaseTableRowsPagination } from "@/hooks/pagination/use-case-table-rows-pagination"
import { CaseTableRowsTable } from "./case-table-rows-table"

interface CaseTableRowsSectionProps {
  caseId: string
  workspaceId: string
}

export function CaseTableRowsSection({
  caseId,
  workspaceId,
}: CaseTableRowsSectionProps) {
  const [rowsPerPage, setRowsPerPage] = useState(10)
  const [accordionValue, setAccordionValue] = useState<string | undefined>(
    "table-rows"
  )

  const {
    data: tableRows,
    isLoading,
    error,
    goToNextPage,
    goToPreviousPage,
    goToFirstPage,
    hasNextPage,
    hasPreviousPage,
    currentPage,
    totalEstimate,
    startItem,
    endItem,
    refetch,
  } = useCaseTableRowsPagination({
    caseId,
    workspaceId,
    limit: rowsPerPage,
  })

  const pagination = {
    currentPage,
    hasNextPage,
    hasPreviousPage,
    pageSize: rowsPerPage,
    totalEstimate: totalEstimate ?? 0,
    startItem,
    endItem,
    onNextPage: goToNextPage,
    onPreviousPage: goToPreviousPage,
    onFirstPage: goToFirstPage,
    onPageSizeChange: (size: number) => {
      setRowsPerPage(size)
      goToFirstPage()
    },
    isLoading,
  }

  if (isLoading && (!tableRows || tableRows.length === 0)) {
    return (
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <Skeleton className="h-8 w-32" />
          <Skeleton className="h-8 w-24" />
        </div>
        <Skeleton className="h-[200px] w-full" />
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center py-8">
        <p className="text-sm text-muted-foreground">
          Failed to load linked table rows
        </p>
        <p className="mt-1 text-xs text-muted-foreground">
          {(error as Error).message}
        </p>
      </div>
    )
  }

  return (
    <Accordion
      type="single"
      collapsible
      value={accordionValue}
      onValueChange={setAccordionValue}
    >
      <AccordionItem value="table-rows" className="border-none px-0">
        <div className="flex items-center justify-between gap-2">
          <AccordionTrigger className="flex-1 justify-between gap-2.5 py-0 hover:no-underline">
            <h3 className="text-sm font-medium text-muted-foreground">Rows</h3>
          </AccordionTrigger>
        </div>
        <AccordionContent className="px-0 pb-0 pt-3">
          <CaseTableRowsTable
            rows={tableRows || []}
            isLoading={isLoading}
            error={error as Error | null}
            caseId={caseId}
            workspaceId={workspaceId}
            pagination={pagination}
            onRefetch={() => {
              refetch()
              setAccordionValue("table-rows")
            }}
          />
        </AccordionContent>
      </AccordionItem>
    </Accordion>
  )
}
