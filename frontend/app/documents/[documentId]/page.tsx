import { DocumentViewer } from "@/components/DocumentViewer";

type DocumentPageProps = {
  params: Promise<{
    documentId: string;
  }>;
};

export default async function DocumentPage({ params }: DocumentPageProps) {
  const { documentId } = await params;

  return <DocumentViewer documentId={documentId} />;
}
