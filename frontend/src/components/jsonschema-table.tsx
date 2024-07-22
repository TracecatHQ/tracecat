import { JSONSchema7 } from "json-schema"
import YAML from "yaml"

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
  const rows = transformJsonSchemaToTableRows(schema)
  return (
    <Table>
      <TableHeader>
        <TableRow className="h-6  text-xs capitalize ">
          <TableHead className="font-bold">Parameter</TableHead>
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
                <TableCell>
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
                    className="max-w-300 w-200 space-y-2 p-3"
                    side="left"
                    align="start"
                    sideOffset={20}
                  >
                    <div className="flex w-full items-center justify-between text-muted-foreground ">
                      <span className="font-mono text-sm font-semibold">
                        {row.parameter}
                      </span>
                      <span className="text-xs text-muted-foreground/80">
                        {row.required ? "(required)" : "(optional)"}
                      </span>
                    </div>
                    <div className="w-full space-y-1">
                      <span className="text-xs font-semibold text-muted-foreground">
                        Description
                      </span>
                      <p className="text-xs text-foreground/70">
                        {row.description}
                      </p>
                    </div>
                    <div className="w-full space-y-1">
                      <span className="text-xs font-semibold text-muted-foreground">
                        Constraints
                      </span>
                      <p className="text-xs text-foreground/70">
                        {row.constraints || "None"}
                      </p>
                    </div>
                    <div className="w-full space-y-1">
                      <span className="text-xs font-semibold text-muted-foreground">
                        Example
                      </span>
                      <div className="rounded-md border bg-muted-foreground/10 p-2">
                        <pre className="text-xs text-foreground/70">
                          {YAML.stringify(
                            { placeholder: "Examples coming soon!" },
                            null,
                            2
                          )}
                        </pre>
                      </div>
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
