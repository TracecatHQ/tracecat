import {
  ChevronLeftIcon,
  ChevronRightIcon,
  DoubleArrowLeftIcon,
} from "@radix-ui/react-icons"

import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

export interface AgGridPaginationProps {
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
  isLoading?: boolean
}

export function AgGridPagination({
  currentPage,
  hasNextPage,
  hasPreviousPage,
  pageSize,
  totalEstimate,
  startItem,
  endItem,
  onNextPage,
  onPreviousPage,
  onFirstPage,
  onPageSizeChange,
  isLoading,
}: AgGridPaginationProps) {
  const totalPages = totalEstimate
    ? Math.ceil(totalEstimate / pageSize)
    : hasNextPage
      ? "..."
      : currentPage + 1

  return (
    <div className="flex items-center justify-between px-2">
      <div className="flex-1 text-xs text-muted-foreground">
        {startItem && endItem && totalEstimate ? (
          <p className="text-xs text-muted-foreground">
            Showing {startItem}-{endItem} of {totalEstimate}
          </p>
        ) : null}
      </div>
      <div className="flex items-center space-x-6 lg:space-x-8">
        <div className="flex items-center space-x-2">
          <p className="text-xs font-medium text-foreground/70">
            Rows per page
          </p>
          <Select
            value={`${pageSize}`}
            onValueChange={(value) => onPageSizeChange(Number(value))}
            disabled={isLoading}
          >
            <SelectTrigger className="h-8 w-[70px]">
              <SelectValue placeholder={pageSize} />
            </SelectTrigger>
            <SelectContent side="top">
              {[10, 20, 50, 75, 100].map((size) => (
                <SelectItem key={size} value={`${size}`}>
                  <p className="text-xs">{size}</p>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex w-[100px] items-center justify-center text-xs font-medium text-foreground/70">
          {totalEstimate ? (
            <span>
              Page {currentPage + 1} of {totalPages}
            </span>
          ) : (
            <span>Page {currentPage + 1}</span>
          )}
        </div>
        <div className="flex items-center space-x-2">
          <Button
            variant="outline"
            className="hidden size-8 p-0 lg:flex"
            onClick={onFirstPage}
            disabled={!hasPreviousPage || isLoading}
          >
            <span className="sr-only">Go to first page</span>
            <DoubleArrowLeftIcon className="size-4" />
          </Button>
          <Button
            variant="outline"
            className="size-8 p-0"
            onClick={onPreviousPage}
            disabled={!hasPreviousPage || isLoading}
          >
            <span className="sr-only">Go to previous page</span>
            <ChevronLeftIcon className="size-4" />
          </Button>
          <Button
            variant="outline"
            className="size-8 p-0"
            onClick={onNextPage}
            disabled={!hasNextPage || isLoading}
          >
            <span className="sr-only">Go to next page</span>
            <ChevronRightIcon className="size-4" />
          </Button>
        </div>
      </div>
    </div>
  )
}
