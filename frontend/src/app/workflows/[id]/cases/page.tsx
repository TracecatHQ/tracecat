import { promises as fs } from "fs"
import path from "path"
import { Metadata } from "next"
import { z } from "zod"

import { caseSchema, type Case } from "@/types/schemas"
import CaseTable from "@/components/cases/table"

export const metadata: Metadata = {
  title: "Cases | Tracecat",
}

async function getCases(): Promise<Case[]> {
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
        <div className="flex-1 space-y-4 px-16 py-24">
          <CaseTable cases={cases} />
        </div>
      </div>
    </>
  )
}
