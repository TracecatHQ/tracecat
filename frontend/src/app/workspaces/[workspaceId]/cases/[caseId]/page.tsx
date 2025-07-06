import { CasePanelView } from "@/components/cases/case-panel-view"

interface CaseDetailPageProps {
  params: Promise<{
    workspaceId: string
    caseId: string
  }>
}

export default async function CaseDetailPage({ params }: CaseDetailPageProps) {
  const { caseId } = await params

  return (
    <div className="flex size-full flex-col">
      <CasePanelView caseId={caseId} />
    </div>
  )
}
