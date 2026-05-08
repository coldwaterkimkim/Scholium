"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import {
  ApiRequestError,
  type DocumentProcessing,
  type ProcessingFailureSummary,
  getDocumentProcessing,
} from "@/lib/api";

import styles from "./ProcessingStatus.module.css";

type ProcessingStatusProps = {
  documentId: string;
};

function getProcessingErrorMessage(error: unknown): string {
  if (error instanceof ApiRequestError) {
    return error.message;
  }

  if (error instanceof Error && error.message) {
    return error.message;
  }

  return "처리 상태를 불러올 수 없어.";
}

function formatStage(snapshot: DocumentProcessing | null): string {
  const stage = snapshot?.stage ?? snapshot?.current_stage;
  return formatStageValue(stage);
}

function formatStageValue(stage: string | null | undefined): string {
  if (!stage) {
    return "-";
  }

  switch (stage) {
    case "render":
      return "render";
    case "pass1":
      return "pass1";
    case "synthesis":
      return "document synthesis";
    case "pass2":
      return "pass2";
    default:
      return stage;
  }
}

function formatFailureSummary(failure: ProcessingFailureSummary): string {
  return `${formatStageValue(failure.stage)} · page ${failure.page_number}`;
}

export function ProcessingStatus({ documentId }: ProcessingStatusProps) {
  const router = useRouter();
  const [snapshot, setSnapshot] = useState<DocumentProcessing | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timeoutId: number | null = null;
    let controller: AbortController | null = null;

    const scheduleNextPoll = () => {
      if (cancelled) {
        return;
      }

      timeoutId = window.setTimeout(() => {
        void pollProcessing();
      }, 2000);
    };

    const pollProcessing = async () => {
      controller?.abort();
      controller = new AbortController();

      try {
        const nextSnapshot = await getDocumentProcessing(documentId, controller.signal);
        if (cancelled) {
          return;
        }

        setSnapshot(nextSnapshot);
        setError(null);
        setLoading(false);

        if (nextSnapshot.status === "completed" || nextSnapshot.status === "failed") {
          return;
        }

        scheduleNextPoll();
      } catch (processingError: unknown) {
        if (cancelled) {
          return;
        }

        if (processingError instanceof DOMException && processingError.name === "AbortError") {
          return;
        }

        setError(getProcessingErrorMessage(processingError));
        setLoading(false);

        if (!(processingError instanceof ApiRequestError && processingError.status === 404)) {
          scheduleNextPoll();
        }
      }
    };

    void pollProcessing();

    return () => {
      cancelled = true;
      if (timeoutId !== null) {
        window.clearTimeout(timeoutId);
      }
      controller?.abort();
    };
  }, [documentId, router]);

  const isFailed = snapshot?.status === "failed";
  const canOpenViewer = Boolean(snapshot?.render_ready_for_viewer || snapshot?.ready_for_viewer);
  const showInlineWarning = Boolean(snapshot?.has_errors && snapshot.status !== "failed");

  return (
    <div className={styles.page}>
      <main className={styles.shell}>
        <section className={styles.surface}>
          <div className={styles.header}>
            <h1 className={styles.title}>문서 처리 상태</h1>
            <p className={styles.description}>
              준비 단계를 확인하고, viewer가 열릴 수 있는 시점부터 직접 들어갈 수 있어.
            </p>
          </div>

          <div className={styles.identityRow}>
            <span className={styles.identityLabel}>document_id</span>
            <span className={styles.identityValue}>{documentId}</span>
          </div>

          {loading && !snapshot ? (
            <div className={styles.infoBox}>처리 상태를 확인하는 중...</div>
          ) : null}

          {error ? (
            <div className={`${styles.infoBox} ${styles.errorBox}`}>
              {error}
            </div>
          ) : null}

          {showInlineWarning ? (
            <div className={`${styles.infoBox} ${styles.warningBox}`}>
              일부 페이지는 실패했지만 처리는 계속 진행 중이야.
            </div>
          ) : null}

          {snapshot ? (
            <div className={styles.grid}>
              <div className={styles.item}>
                <span className={styles.label}>status</span>
                <span className={styles.value}>{snapshot.status}</span>
              </div>
              <div className={styles.item}>
                <span className={styles.label}>stage</span>
                <span className={styles.value}>{formatStage(snapshot)}</span>
              </div>
              <div className={styles.item}>
                <span className={styles.label}>rendered_pages</span>
                <span className={styles.value}>
                  {snapshot.rendered_pages} / {snapshot.total_pages ?? "-"}
                </span>
              </div>
              <div className={styles.item}>
                <span className={styles.label}>pass1_completed_pages</span>
                <span className={styles.value}>{snapshot.pass1_completed_pages}</span>
              </div>
              <div className={styles.item}>
                <span className={styles.label}>pass1_failed_pages</span>
                <span className={styles.value}>{snapshot.pass1_failed_pages}</span>
              </div>
              <div className={styles.item}>
                <span className={styles.label}>pass1_processed_pages</span>
                <span className={styles.value}>
                  {snapshot.pass1_processed_pages} / {snapshot.rendered_pages}
                </span>
              </div>
              <div className={styles.item}>
                <span className={styles.label}>pass2_completed_pages</span>
                <span className={styles.value}>{snapshot.pass2_completed_pages}</span>
              </div>
              <div className={styles.item}>
                <span className={styles.label}>pass2_failed_pages</span>
                <span className={styles.value}>{snapshot.pass2_failed_pages}</span>
              </div>
              <div className={styles.item}>
                <span className={styles.label}>has_errors</span>
                <span className={styles.value}>{snapshot.has_errors ? "true" : "false"}</span>
              </div>
              <div className={styles.item}>
                <span className={styles.label}>render_ready_for_viewer</span>
                <span className={styles.value}>{snapshot.render_ready_for_viewer ? "true" : "false"}</span>
              </div>
              <div className={styles.item}>
                <span className={styles.label}>page_context_ready_pages</span>
                <span className={styles.value}>{snapshot.page_context_ready_pages}</span>
              </div>
              <div className={styles.item}>
                <span className={styles.label}>document_context_ready</span>
                <span className={styles.value}>{snapshot.document_context_ready ? "true" : "false"}</span>
              </div>
              <div className={styles.item}>
                <span className={styles.label}>current_page_number</span>
                <span className={styles.value}>{snapshot.current_page_number ?? "-"}</span>
              </div>
            </div>
          ) : null}

          <div className={styles.actionRow}>
            <button type="button" className={styles.secondaryButton} onClick={() => router.push("/")}>
              작업 목록
            </button>
            <button
              type="button"
              className={styles.primaryButton}
              onClick={() => router.push(`/documents/${encodeURIComponent(documentId)}`)}
              disabled={!canOpenViewer}
            >
              viewer 열기
            </button>
          </div>

          {snapshot?.error_message ? (
            <div className={`${styles.infoBox} ${isFailed ? styles.errorBox : styles.warningBox}`}>
              {snapshot.error_message}
            </div>
          ) : null}

          {snapshot?.recent_failures?.length ? (
            <div className={styles.failureSection}>
              <div className={styles.failureTitle}>recent_failures</div>
              <div className={styles.failureList}>
                {snapshot.recent_failures.map((failure) => (
                  <div
                    key={`${failure.stage}:${failure.page_number}:${failure.error_message}`}
                    className={styles.failureItem}
                  >
                    <div className={styles.failureMeta}>{formatFailureSummary(failure)}</div>
                    <div>{failure.error_message}</div>
                  </div>
                ))}
              </div>
            </div>
          ) : null}

          {isFailed ? (
            <div className={styles.failureNote}>
              처리에 실패해서 viewer로 이동하지 않았어. 지금 단계에선 재시도 버튼은 아직 없어.
            </div>
          ) : null}
        </section>
      </main>
    </div>
  );
}
