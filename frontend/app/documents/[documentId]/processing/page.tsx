import { ProcessingStatus } from "@/components/ProcessingStatus";

type DocumentProcessingPageProps = {
  params: Promise<{
    documentId: string;
  }>;
};

export default async function DocumentProcessingPage({ params }: DocumentProcessingPageProps) {
  const { documentId } = await params;

  return <ProcessingStatus documentId={documentId} />;
}
