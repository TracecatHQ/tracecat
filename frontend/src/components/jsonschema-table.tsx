import { JSONSchema7 } from "json-schema"

import { transformJsonSchemaToTableRows } from "@/lib/jsonschema"
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
        <TableRow className="grid h-6 grid-cols-3 text-xs capitalize ">
          <TableHead className="col-span-1 font-bold">Parameter</TableHead>
          <TableHead className="col-span-1 font-bold">Type</TableHead>
          <TableHead className="col-span-1 font-bold">Default</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row, idx) => (
          <TableRow
            key={idx}
            className="grid grid-cols-3 text-xs text-muted-foreground"
          >
            <TableCell className="col-span-1">{row.parameter}</TableCell>
            <TableCell className="col-span-1">{row.type}</TableCell>
            <TableCell className="col-span-1">{row.default}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}
