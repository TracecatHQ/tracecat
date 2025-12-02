import {
  ChevronLeftIcon,
  ChevronRightIcon,
  DoubleArrowLeftIcon,
  DoubleArrowRightIcon,
} from "@radix-ui/react-icons"
import type { Table } from "@tanstack/react-table"

import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

export interface ServerSidePaginationProps {
  currentPage: number
  hasNextPage: boolean
  hasPreviousPage: boolean
  pageSize: number
  totalEstimate?: number
  startItem?: number
  endItem?: number
  onNextPage: () => void
  onPreviousPage: () => void
  onFirstPage: () => void
  onPageSizeChange: (pageSize: number) => void
  onSortingChange?: (
    columnId: string,
    direction: "asc" | "desc" | false
  ) => void
  isLoading?: boolean
}

interface DataTablePaginationProps<TData> {
  table: Table<TData>
  showSelectedRows?: boolean
  serverSide?: ServerSidePaginationProps
}

export function DataTablePagination<TData>({
  table,
  showSelectedRows,
  serverSide,
}: DataTablePaginationProps<TData>) {
  const isServerSide = !!serverSide

  // Use server-side values when available, otherwise use table values
  const currentPageSize = isServerSide
    ? serverSide.pageSize
    : table.getState().pagination.pageSize
  const currentPageIndex = isServerSide
    ? serverSide.currentPage
    : table.getState().pagination.pageIndex
  const canPreviousPage = isServerSide
    ? serverSide.hasPreviousPage
    : table.getCanPreviousPage()
  const canNextPage = isServerSide
    ? serverSide.hasNextPage
    : table.getCanNextPage()
  const totalPages = isServerSide
    ? serverSide.totalEstimate
      ? Math.ceil(serverSide.totalEstimate / serverSide.pageSize)
      : serverSide.hasNextPage
        ? "..."
        : currentPageIndex + 1
    : table.getPageCount()
  const isLoading = isServerSide ? serverSide.isLoading : false
  return (
    <div className="flex items-center justify-between px-2">
      <div className="flex-1 text-xs text-muted-foreground">
        {showSelectedRows ? (
          <p>
            {table.getFilteredSelectedRowModel().rows.length} of{" "}
            {table.getFilteredRowModel().rows.length} row(s) selected.
          </p>
        ) : isServerSide &&
          serverSide.startItem &&
          serverSide.endItem &&
          serverSide.totalEstimate ? (
          <p className="text-xs text-muted-foreground">
            Showing {serverSide.startItem}-{serverSide.endItem} of{" "}
            {serverSide.totalEstimate}
          </p>
        ) : null}
      </div>
      <div className="flex items-center space-x-6 lg:space-x-8">
        <div className="flex items-center space-x-2">
          <p className="text-xs font-medium text-foreground/70">
            Rows per page
          </p>
          <Select
            value={`${currentPageSize}`}
            onValueChange={(value) => {
              const newPageSize = Number(value)
              if (isServerSide) {
                serverSide.onPageSizeChange(newPageSize)
              } else {
                table.setPageSize(newPageSize)
              }
            }}
            disabled={isLoading}
          >
            <SelectTrigger className="h-8 w-[70px]">
              <SelectValue placeholder={currentPageSize} />
            </SelectTrigger>
            <SelectContent side="top">
              {[10, 20, 50, 75, 100].map((pageSize) => (
                <SelectItem key={pageSize} value={`${pageSize}`}>
                  <p className="text-xs">{pageSize}</p>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex w-[100px] items-center justify-center text-xs font-medium text-foreground/70">
          {isServerSide ? (
            serverSide.totalEstimate ? (
              <span>
                Page {currentPageIndex + 1} of {totalPages}
              </span>
            ) : (
              <span>Page {currentPageIndex + 1}</span>
            )
          ) : (
            <span>
              Page {currentPageIndex + 1} of {totalPages}
            </span>
          )}
        </div>
        <div className="flex items-center space-x-2">
          <Button
            variant="outline"
            className="hidden size-8 p-0 lg:flex"
            onClick={() => {
              if (isServerSide) {
                serverSide.onFirstPage()
              } else {
                table.setPageIndex(0)
              }
            }}
            disabled={!canPreviousPage || isLoading}
          >
            <span className="sr-only">Go to first page</span>
            <DoubleArrowLeftIcon className="size-4" />
          </Button>
          <Button
            variant="outline"
            className="size-8 p-0"
            onClick={() => {
              if (isServerSide) {
                serverSide.onPreviousPage()
              } else {
                table.previousPage()
              }
            }}
            disabled={!canPreviousPage || isLoading}
          >
            <span className="sr-only">Go to previous page</span>
            <ChevronLeftIcon className="size-4" />
          </Button>
          <Button
            variant="outline"
            className="size-8 p-0"
            onClick={() => {
              if (isServerSide) {
                serverSide.onNextPage()
              } else {
                table.nextPage()
              }
            }}
            disabled={!canNextPage || isLoading}
          >
            <span className="sr-only">Go to next page</span>
            <ChevronRightIcon className="size-4" />
          </Button>
          {!isServerSide && (
            <Button
              variant="outline"
              className="hidden size-8 p-0 lg:flex"
              onClick={() => table.setPageIndex(table.getPageCount() - 1)}
              disabled={!canNextPage || isLoading}
            >
              <span className="sr-only">Go to last page</span>
              <DoubleArrowRightIcon className="size-4" />
            </Button>
          )}
        </div>
      </div>
    </div>
  )
}
