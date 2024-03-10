import { promises as fs } from "fs"
import path from "path"
import { Metadata } from "next"
import { DefaultQueryClientProvider } from "@/providers/query"
import { WorkflowProvider } from "@/providers/workflow"
import { z } from "zod"

import { columns } from "@/components/cases/columns"
import { DataTable } from "@/components/cases/data-table"
import { caseSchema } from "@/components/cases/data/schema"
import { Navbar } from "@/components/navbar"

export const metadata: Metadata = {
  title: "Cases | Tracecat",
}

async function getCases() {
  const data = await fs.readFile(
    path.join(process.cwd(), "src/components/cases/data/cases.json")
  )
  const cases = JSON.parse(data.toString())
  return z.array(caseSchema).parse(cases)
}

export default async function CasesPage() {
  const cases = await getCases()

  return (
    <>
      <div className="flex h-screen flex-col">
        <div className="flex-1 px-16 py-24">
          <DataTable data={cases} columns={columns} />
        </div>
      </div>
    </>
  )
}
