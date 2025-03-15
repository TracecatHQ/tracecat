import type { JSONSchema7 } from "json-schema"

import { transformJsonSchemaToTableRows } from "@/lib/jsonschema"
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"

export function JSONSchemaTable({ schema }: { schema: JSONSchema7 }) {
  const rows = transformJsonSchemaToTableRows(schema).sort((a, b) => {
    // Sort required fields first, then alphabetically
    if (a.required && !b.required) return -1
    if (!a.required && b.required) return 1
    return a.parameter.localeCompare(b.parameter)
  })
  return (
    <Table>
      <TableHeader>
        <TableRow className="h-6 text-xs capitalize ">
          <TableHead className="min-w-max whitespace-nowrap font-bold">
            Parameter
          </TableHead>
          <TableHead className="font-bold">Type</TableHead>
          <TableHead className="font-bold">Default</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row, idx) => (
          <HoverCard openDelay={100} closeDelay={100} key={idx}>
            <HoverCardTrigger asChild className="hover:border-none">
              <TableRow
                key={idx}
                className="font-mono text-xs tracking-tight text-muted-foreground"
              >
                <TableCell className="whitespace-nowrap">
                  {row.parameter}
                  {row.required && " *"}
                </TableCell>
                <TableCell>{row.type}</TableCell>
                <TableCell>
                  {typeof row.default === "object"
                    ? JSON.stringify(row.default)
                    : row.default}
                  {/* This is a hacky solution to avoid `<div> cannot appear as a child of <tr>` */}
                  <HoverCardContent
                    className="w-[300px] max-w-[300px] space-y-2 p-3"
                    side="left"
                    align="start"
                    sideOffset={20}
                  >
                    <div className="flex w-full items-center justify-between text-muted-foreground">
                      <span className="break-words font-mono text-sm font-semibold">
                        {row.parameter}
                      </span>
                      <span className="whitespace-nowrap text-xs text-muted-foreground/80">
                        &nbsp;{row.required ? "(required)" : "(optional)"}
                      </span>
                    </div>
                    <div className="w-full space-y-1">
                      <span className="text-xs font-semibold text-muted-foreground">
                        Description
                      </span>
                      <p className="break-words text-xs text-foreground/70">
                        {row.description}
                      </p>
                    </div>
                    <div className="w-full space-y-1">
                      <span className="text-xs font-semibold text-muted-foreground">
                        Constraints
                      </span>
                      <p className="break-words text-xs text-foreground/70">
                        {row.constraints || "None"}
                      </p>
                    </div>
                  </HoverCardContent>
                </TableCell>
              </TableRow>
            </HoverCardTrigger>
          </HoverCard>
        ))}
      </TableBody>
    </Table>
  )
}
