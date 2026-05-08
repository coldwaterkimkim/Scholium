"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import {
  ApiRequestError,
  deleteDocument,
  type DocumentListItem,
  listDocuments,
  uploadDocument,
} from "@/lib/api";

import styles from "./UploadForm.module.css";

type RefreshOptions = {
  signal?: AbortSignal;
  showLoading?: boolean;
};

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

function isPreparing(document: DocumentListItem): boolean {
  return ["uploaded", "rendering", "analyzing"].includes(document.status);
}

function canOpenViewer(document: DocumentListItem): boolean {
  return document.status === "completed";
}

function getStatusLabel(status: string): string {
  switch (status) {
    case "completed":
      return "완료";
    case "failed":
      return "실패";
    case "uploaded":
    case "rendering":
    case "analyzing":
      return "준비 중";
    default:
      return status;
  }
}

function getStatusDetail(document: DocumentListItem): string {
  switch (document.status) {
    case "uploaded":
      return "업로드됨";
    case "rendering":
      return "렌더링";
    case "analyzing":
      return "분석 중";
    default:
      return "";
  }
}

function getDocumentPath(document: DocumentListItem): string {
  const encodedDocumentId = encodeURIComponent(document.document_id);
  if (canOpenViewer(document)) {
    return `/documents/${encodedDocumentId}`;
  }

  return `/documents/${encodedDocumentId}/processing`;
}

function getProcessingPath(document: DocumentListItem): string {
  return `/documents/${encodeURIComponent(document.document_id)}/processing`;
}

function getElapsedSeconds(document: DocumentListItem, now: Date): number {
  const startedAt = new Date(document.created_at).getTime();
  const endedAt = isPreparing(document) ? now.getTime() : new Date(document.updated_at).getTime();

  if (Number.isNaN(startedAt) || Number.isNaN(endedAt)) {
    return 0;
  }

  return Math.max(0, Math.floor((endedAt - startedAt) / 1000));
}

function formatDuration(totalSeconds: number): string {
  const safeSeconds = Math.max(0, Math.floor(totalSeconds));
  const hours = Math.floor(safeSeconds / 3600);
  const minutes = Math.floor((safeSeconds % 3600) / 60);
  const seconds = safeSeconds % 60;

  if (hours > 0) {
    return `${hours}h ${minutes}m ${seconds}s`;
  }

  if (minutes > 0) {
    return `${minutes}m ${seconds}s`;
  }

  return `${seconds}s`;
}

export function UploadForm() {
  const router = useRouter();
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [fileInputKey, setFileInputKey] = useState(0);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [documents, setDocuments] = useState<DocumentListItem[]>([]);
  const [isListLoading, setIsListLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [selectedDocumentIds, setSelectedDocumentIds] = useState<Set<string>>(() => new Set());
  const [deletingDocumentIds, setDeletingDocumentIds] = useState<Set<string>>(() => new Set());
  const [now, setNow] = useState(() => new Date());

  const hasDocuments = documents.length > 0;
  const selectedCount = selectedDocumentIds.size;
  const completedCount = useMemo(
    () => documents.filter((document) => document.status === "completed").length,
    [documents],
  );
  const preparingCount = useMemo(
    () => documents.filter((document) => isPreparing(document)).length,
    [documents],
  );
  const hasPreparingDocuments = preparingCount > 0;

  async function refreshDocuments(options: RefreshOptions = {}) {
    const showLoading = options.showLoading ?? true;
    if (showLoading) {
      setIsListLoading(true);
    }
    setListError(null);

    try {
      const result = await listDocuments(options.signal);
      setDocuments(result.documents);
      setSelectedDocumentIds((currentSelection) => {
        const knownDocumentIds = new Set(result.documents.map((document) => document.document_id));
        return new Set([...currentSelection].filter((documentId) => knownDocumentIds.has(documentId)));
      });
    } catch (listFetchError: unknown) {
      if (listFetchError instanceof DOMException && listFetchError.name === "AbortError") {
        return;
      }

      setListError(getListErrorMessage(listFetchError));
    } finally {
      if (!options.signal?.aborted) {
        setIsListLoading(false);
      }
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    void refreshDocuments({ signal: controller.signal });

    return () => {
      controller.abort();
    };
  }, []);

  useEffect(() => {
    const timerId = window.setInterval(() => {
      setNow(new Date());
    }, 1000);

    return () => {
      window.clearInterval(timerId);
    };
  }, []);

  useEffect(() => {
    if (!hasPreparingDocuments) {
      return;
    }

    const timerId = window.setInterval(() => {
      void refreshDocuments({ showLoading: false });
    }, 2500);

    return () => {
      window.clearInterval(timerId);
    };
  }, [hasPreparingDocuments]);

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
    setNotice(null);
    setIsSubmitting(true);

    try {
      const uploadedFileName = selectedFile.name;
      await uploadDocument(selectedFile);
      setSelectedFile(null);
      setFileInputKey((currentKey) => currentKey + 1);
      setNotice(`${uploadedFileName} 작업을 목록에 추가했어. 같은 파일명이 있으면 기존 작업을 덮어써.`);
      await refreshDocuments({ showLoading: false });
    } catch (uploadError: unknown) {
      setError(getUploadErrorMessage(uploadError));
    } finally {
      setIsSubmitting(false);
    }
  }

  function handleFileChange(event: React.ChangeEvent<HTMLInputElement>) {
    const nextFile = event.target.files?.[0] ?? null;
    setSelectedFile(nextFile);
    setError(null);
    setNotice(null);
  }

  function toggleDocumentSelection(documentId: string, checked: boolean) {
    setSelectedDocumentIds((currentSelection) => {
      const nextSelection = new Set(currentSelection);
      if (checked) {
        nextSelection.add(documentId);
      } else {
        nextSelection.delete(documentId);
      }
      return nextSelection;
    });
  }

  function clearSelection() {
    setSelectedDocumentIds(new Set());
  }

  async function deleteDocuments(documentIds: string[]) {
    if (documentIds.length === 0) {
      return;
    }

    const confirmed = window.confirm(
      `${documentIds.length}개 문서를 삭제할까? 원본 PDF, 렌더 이미지, 분석 결과가 함께 정리돼.`,
    );
    if (!confirmed) {
      return;
    }

    setError(null);
    setNotice(null);
    setDeletingDocumentIds((currentIds) => new Set([...currentIds, ...documentIds]));

    try {
      for (const documentId of documentIds) {
        await deleteDocument(documentId);
      }
      setNotice(`${documentIds.length}개 문서를 삭제했어.`);
      setSelectedDocumentIds((currentSelection) => {
        const nextSelection = new Set(currentSelection);
        for (const documentId of documentIds) {
          nextSelection.delete(documentId);
        }
        return nextSelection;
      });
      await refreshDocuments({ showLoading: false });
    } catch (deleteError: unknown) {
      setListError(getListErrorMessage(deleteError));
    } finally {
      setDeletingDocumentIds((currentIds) => {
        const nextIds = new Set(currentIds);
        for (const documentId of documentIds) {
          nextIds.delete(documentId);
        }
        return nextIds;
      });
    }
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
              PDF를 올리면 작업 목록에 추가되고, 준비가 끝난 문서는 viewer에서 드래그 선택 설명을 볼 수 있어.
            </p>
          </div>

          <form className={styles.form} onSubmit={handleSubmit}>
            <div className={styles.fileField}>
              <span className={styles.fieldLabel}>PDF 파일</span>
              <input
                key={fileInputKey}
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
                    {selectedFile
                      ? "같은 파일명이 이미 있으면 기존 작업을 덮어써."
                      : "업로드 후 바로 viewer로 이동하지 않고 목록에서 준비 상태를 보여줘."}
                  </span>
                </span>
                <span className={styles.filePickerAction}>찾아보기</span>
              </label>
            </div>

            <div className={styles.fileInfo}>
              {selectedFile ? selectedFile.name : "아직 선택된 파일이 없어."}
            </div>

            {error ? <div className={styles.errorBox}>{error}</div> : null}
            {notice ? <div className={styles.noticeBox}>{notice}</div> : null}

            <button type="submit" className={styles.submitButton} disabled={isSubmitting}>
              {isSubmitting ? "목록에 추가하는 중..." : "작업 목록에 추가"}
            </button>
          </form>
        </section>

        <section className={`${styles.surface} ${styles.worklistSurface}`}>
          <div className={styles.worklistHeader}>
            <div>
              <h2 className={styles.sectionTitle}>작업 목록</h2>
              <p className={styles.sectionDescription}>
                {hasDocuments
                  ? `${documents.length}개 문서 · 완료 ${completedCount}개 · 준비 중 ${preparingCount}개`
                  : "아직 저장된 문서가 없어."}
              </p>
            </div>
            <div className={styles.worklistActions}>
              {selectedCount > 0 ? (
                <>
                  <button type="button" className={styles.refreshButton} onClick={clearSelection}>
                    선택 해제
                  </button>
                  <button
                    type="button"
                    className={`${styles.refreshButton} ${styles.dangerButton}`}
                    onClick={() => {
                      void deleteDocuments([...selectedDocumentIds]);
                    }}
                    disabled={deletingDocumentIds.size > 0}
                  >
                    선택 삭제 {selectedCount}
                  </button>
                </>
              ) : null}
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
              {documents.map((document) => {
                const isDeleting = deletingDocumentIds.has(document.document_id);
                const elapsedSeconds = getElapsedSeconds(document, now);
                const durationLabel = isPreparing(document)
                  ? `준비 중 ${formatDuration(elapsedSeconds)}`
                  : `준비 시간 ${formatDuration(elapsedSeconds)}`;
                const statusDetail = getStatusDetail(document);

                return (
                  <article key={document.document_id} className={styles.documentRow}>
                    <label className={styles.selectionCell}>
                      <input
                        type="checkbox"
                        checked={selectedDocumentIds.has(document.document_id)}
                        onChange={(event) => {
                          toggleDocumentSelection(document.document_id, event.target.checked);
                        }}
                        disabled={isDeleting}
                      />
                      <span className={styles.selectionLabel}>선택</span>
                    </label>

                    <div className={styles.documentMain}>
                      <div className={styles.documentTopline}>
                        <span className={styles.documentName}>{document.filename}</span>
                        <span className={`${styles.statusPill} ${styles[`status_${document.status}`] ?? ""}`}>
                          {getStatusLabel(document.status)}
                        </span>
                      </div>
                      <div className={styles.documentMeta}>
                        {document.total_pages ? `${document.total_pages} pages` : "pages pending"} · updated{" "}
                        {formatUpdatedAt(document.updated_at)}
                      </div>
                      <div className={styles.documentProgress}>
                        <span>{durationLabel}</span>
                        {statusDetail ? <span>{statusDetail}</span> : null}
                      </div>
                      {document.error_message ? (
                        <div className={styles.documentError}>{document.error_message}</div>
                      ) : null}
                    </div>

                    <div className={styles.documentActions}>
                      <button
                        type="button"
                        className={styles.rowButton}
                        onClick={() => router.push(getDocumentPath(document))}
                      >
                        {canOpenViewer(document) ? "열기" : "상태"}
                      </button>
                      <button
                        type="button"
                        className={styles.rowButton}
                        onClick={() => router.push(getProcessingPath(document))}
                      >
                        처리 상태
                      </button>
                      <button
                        type="button"
                        className={`${styles.rowButton} ${styles.deleteButton}`}
                        onClick={() => {
                          void deleteDocuments([document.document_id]);
                        }}
                        disabled={isDeleting}
                      >
                        {isDeleting ? "삭제 중" : "삭제"}
                      </button>
                    </div>
                  </article>
                );
              })}
            </div>
          ) : null}
        </section>
      </main>
    </div>
  );
}
