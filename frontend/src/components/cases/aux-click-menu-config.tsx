import * as React from "react"
import { CaseRead } from "@/client"
import { Column, Table } from "@tanstack/react-table"
import { Sparkles } from "lucide-react"

import { AuxClickMenuOptionProps } from "@/components/aux-click-menu"

type TableCol = {
  table: Table<CaseRead>
  column: Column<CaseRead>
}
export const tableHeaderAuxOptions: AuxClickMenuOptionProps<TableCol>[] = [
  {
    type: "item",
    children: (
      <div className="flex items-center">
        <span>AI Autofill</span>
        <Sparkles className="ml-2 size-4" />
      </div>
    ),
    action: async ({ table, column }, client) => {
      console.log("AI Autofill")
      if (!client) {
        console.error("No client provided")
        return
      }
      /**
       * Steps to perform AI autofill:
       * 1. Figure out with fields need to be populated:
       * 2. Get data for those fields
       * 3.
       */
      console.log("Column ID", column.id)
      const allTableData = table
        .getRowModel()
        .rows.map((row) => row.getValue(column.id))
      console.log("Table Data", allTableData)
      // Getting data for a specific column across all rows
      // const columnData = allTableData.map((row) => row[column.id])
      // console.log("Column Data", columnData)
      const response = await client.post(
        "/completions/cases/stream",
        JSON.stringify([]),
        {
          headers: {
            "Content-Type": "application/json",
          },
        }
      )
      console.log("Response", response.data)

      // Update the table values
    },
  },
]
