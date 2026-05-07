"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import {
  ApiRequestError,
  type DocumentListItem,
  listDocuments,
  uploadDocument,
} from "@/lib/api";

import styles from "./UploadForm.module.css";

function getUploadErrorMessage(error: unknown): string {
  if (error instanceof ApiRequestError) {
    return error.message;
  }

  if (error instanceof Error && error.message) {
    return error.message;
  }

  return "업로드에 실패했어.";
}

function getListErrorMessage(error: unknown): string {
  if (error instanceof ApiRequestError) {
    return error.message;
  }

  if (error instanceof Error && error.message) {
    return error.message;
  }

  return "작업 목록을 불러올 수 없어.";
}

function formatUpdatedAt(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "-";
  }

  return new Intl.DateTimeFormat("ko-KR", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function getStatusLabel(status: string): string {
  switch (status) {
    case "completed":
      return "완료";
    case "failed":
      return "실패";
    case "uploaded":
      return "업로드됨";
    case "rendering":
      return "렌더링";
    case "analyzing":
      return "분석 중";
    default:
      return status;
  }
}

function getDocumentPath(document: DocumentListItem): string {
  const encodedDocumentId = encodeURIComponent(document.document_id);
  if (document.status === "completed") {
    return `/documents/${encodedDocumentId}`;
  }

  return `/documents/${encodedDocumentId}/processing`;
}

export function UploadForm() {
  const router = useRouter();
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [documents, setDocuments] = useState<DocumentListItem[]>([]);
  const [isListLoading, setIsListLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);

  const hasDocuments = documents.length > 0;
  const completedCount = useMemo(
    () => documents.filter((document) => document.status === "completed").length,
    [documents],
  );

  async function refreshDocuments(signal?: AbortSignal) {
    setIsListLoading(true);
    setListError(null);

    try {
      const result = await listDocuments(signal);
      setDocuments(result.documents);
    } catch (listFetchError: unknown) {
      if (listFetchError instanceof DOMException && listFetchError.name === "AbortError") {
        return;
      }

      setListError(getListErrorMessage(listFetchError));
    } finally {
      if (!signal?.aborted) {
        setIsListLoading(false);
      }
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    void refreshDocuments(controller.signal);

    return () => {
      controller.abort();
    };
  }, []);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!selectedFile) {
      setError("PDF 파일을 먼저 선택해.");
      return;
    }

    const fileName = selectedFile.name.toLowerCase();
    if (!fileName.endsWith(".pdf")) {
      setError("PDF 파일만 업로드할 수 있어.");
      return;
    }

    setError(null);
    setIsSubmitting(true);

    try {
      const result = await uploadDocument(selectedFile);
      router.push(`/documents/${encodeURIComponent(result.document_id)}/processing`);
    } catch (uploadError: unknown) {
      setError(getUploadErrorMessage(uploadError));
      setIsSubmitting(false);
    }
  }

  function handleFileChange(event: React.ChangeEvent<HTMLInputElement>) {
    const nextFile = event.target.files?.[0] ?? null;
    setSelectedFile(nextFile);
    setError(null);
  }

  return (
    <div className={styles.page}>
      <main className={styles.shell}>
        <section className={`${styles.surface} ${styles.uploadSurface}`}>
          <div className={styles.header}>
            <div>
              <p className={styles.eyebrow}>Scholium MVP</p>
              <h1 className={styles.title}>문서 작업 홈</h1>
            </div>
            <p className={styles.description}>
              PDF 1개를 올리면 render, page/document preprocessing, synthesis가 실행되고 드래그 선택 설명을 준비해.
            </p>
          </div>

          <form className={styles.form} onSubmit={handleSubmit}>
            <div className={styles.fileField}>
              <span className={styles.fieldLabel}>PDF 파일</span>
              <input
                id="pdf-upload-input"
                className={styles.fileInput}
                type="file"
                accept="application/pdf,.pdf"
                onChange={handleFileChange}
                disabled={isSubmitting}
              />
              <label
                className={`${styles.filePicker} ${isSubmitting ? styles.filePickerDisabled : ""}`}
                htmlFor="pdf-upload-input"
                aria-disabled={isSubmitting}
              >
                <span className={styles.filePickerMain}>
                  <span className={styles.filePickerTitle}>
                    {selectedFile ? selectedFile.name : "PDF 파일 선택"}
                  </span>
                  <span className={styles.filePickerHint}>
                    {selectedFile ? "다른 PDF로 바꾸려면 다시 선택해." : "문서를 열기 전에 먼저 PDF 하나를 골라줘."}
                  </span>
                </span>
                <span className={styles.filePickerAction}>찾아보기</span>
              </label>
            </div>

            <div className={styles.fileInfo}>
              {selectedFile ? selectedFile.name : "아직 선택된 파일이 없어."}
            </div>

            {error ? <div className={styles.errorBox}>{error}</div> : null}

            <button type="submit" className={styles.submitButton} disabled={isSubmitting}>
              {isSubmitting ? "업로드 중..." : "업로드하고 처리 시작"}
            </button>
          </form>
        </section>

        <section className={`${styles.surface} ${styles.worklistSurface}`}>
          <div className={styles.worklistHeader}>
            <div>
              <h2 className={styles.sectionTitle}>작업 목록</h2>
              <p className={styles.sectionDescription}>
                {hasDocuments
                  ? `${documents.length}개 문서 · 완료 ${completedCount}개`
                  : "아직 저장된 문서가 없어."}
              </p>
            </div>
            <button
              type="button"
              className={styles.refreshButton}
              onClick={() => {
                void refreshDocuments();
              }}
              disabled={isListLoading}
            >
              새로고침
            </button>
          </div>

          {isListLoading && !hasDocuments ? (
            <div className={styles.listState}>작업 목록을 불러오는 중...</div>
          ) : null}

          {listError ? (
            <div className={`${styles.listState} ${styles.listError}`}>{listError}</div>
          ) : null}

          {!isListLoading && !listError && !hasDocuments ? (
            <div className={styles.emptyState}>
              첫 PDF를 업로드하면 여기에 작업 기록이 쌓여.
            </div>
          ) : null}

          {hasDocuments ? (
            <div className={styles.documentList}>
              {documents.map((document) => (
                <button
                  key={document.document_id}
                  type="button"
                  className={styles.documentRow}
                  onClick={() => router.push(getDocumentPath(document))}
                >
                  <span className={styles.documentMain}>
                    <span className={styles.documentName}>{document.filename}</span>
                    <span className={styles.documentMeta}>
                      {document.total_pages ? `${document.total_pages} pages` : "pages pending"} · updated{" "}
                      {formatUpdatedAt(document.updated_at)}
                    </span>
                    {document.error_message ? (
                      <span className={styles.documentError}>{document.error_message}</span>
                    ) : null}
                  </span>
                  <span className={`${styles.statusPill} ${styles[`status_${document.status}`] ?? ""}`}>
                    {getStatusLabel(document.status)}
                  </span>
                </button>
              ))}
            </div>
          ) : null}
        </section>
      </main>
    </div>
  );
}
