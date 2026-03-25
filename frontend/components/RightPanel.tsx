import type { FinalAnchor } from "@/lib/api";

import styles from "./RightPanel.module.css";

type RightPanelProps = {
  documentTitle: string;
  filename: string | null;
  isSummaryLoading: boolean;
  summaryError: string | null;
  pageRole: string | null;
  pageSummary: string | null;
  pageRiskNote: string | null;
  isPageLoading: boolean;
  pageError: string | null;
  currentPage: number;
  availableAnchorCount: number;
  selectedAnchor: FinalAnchor | null;
  onNavigateToRelatedPage: (pageNumber: number, anchorId: string) => void;
};

export function RightPanel({
  documentTitle,
  filename,
  isSummaryLoading,
  summaryError,
  pageRole,
  pageSummary,
  pageRiskNote,
  isPageLoading,
  pageError,
  currentPage,
  availableAnchorCount,
  selectedAnchor,
  onNavigateToRelatedPage,
}: RightPanelProps) {
  const relatedPages =
    selectedAnchor?.related_pages.filter((pageNumber) => pageNumber !== currentPage) ?? [];
  const showAnchorDetailSection = availableAnchorCount > 0;
  const confidenceText =
    selectedAnchor && Number.isFinite(selectedAnchor.confidence)
      ? `${Math.round(selectedAnchor.confidence * 100)}%`
      : null;

  return (
    <aside className={styles.panel}>
      <section className={styles.section}>
        <span className={styles.label}>Document</span>
        <h1 className={styles.title}>{documentTitle}</h1>
        {filename ? <p className={styles.subtitle}>{filename}</p> : null}
        {isSummaryLoading ? (
          <p className={styles.mutedText}>문서 요약을 불러오는 중...</p>
        ) : summaryError ? (
          <div className={`${styles.fallback} ${styles.error}`}>{summaryError}</div>
        ) : null}
      </section>

      <section className={styles.section}>
        <span className={styles.label}>Page Role</span>
        {isPageLoading ? (
          <p className={styles.mutedText}>페이지 정보를 불러오는 중...</p>
        ) : pageError ? (
          <div className={`${styles.fallback} ${styles.error}`}>{pageError}</div>
        ) : pageRole ? (
          <p className={styles.text}>{pageRole}</p>
        ) : (
          <div className={styles.fallback}>현재 페이지 역할 정보가 아직 없어.</div>
        )}
      </section>

      <section className={styles.section}>
        <span className={styles.label}>Page Summary</span>
        {isPageLoading ? (
          <p className={styles.mutedText}>페이지 요약을 불러오는 중...</p>
        ) : pageError ? (
          <div className={`${styles.fallback} ${styles.error}`}>페이지 요약을 표시할 수 없어.</div>
        ) : pageSummary ? (
          <p className={styles.text}>{pageSummary}</p>
        ) : (
          <div className={styles.fallback}>페이지 요약 정보가 아직 없어.</div>
        )}
      </section>

      <section className={styles.section}>
        <span className={styles.label}>Page Risk Note</span>
        {isPageLoading ? (
          <p className={styles.mutedText}>페이지 리스크 노트를 불러오는 중...</p>
        ) : pageError ? (
          <div className={`${styles.fallback} ${styles.error}`}>페이지 리스크 노트를 표시할 수 없어.</div>
        ) : pageRiskNote ? (
          <p className={styles.text}>{pageRiskNote}</p>
        ) : (
          <div className={styles.fallback}>페이지 리스크 노트 정보가 아직 없어.</div>
        )}
      </section>

      {showAnchorDetailSection ? (
        <section className={styles.section}>
          <span className={styles.label}>Anchor Details</span>
          {selectedAnchor ? (
            <div className={styles.anchorDetails}>
              <div className={styles.detailBlock}>
                <span className={styles.detailTitle}>Label</span>
                <p className={styles.text}>{selectedAnchor.label}</p>
              </div>

              <div className={styles.detailBlock}>
                <span className={styles.detailTitle}>Question</span>
                <p className={styles.text}>{selectedAnchor.question}</p>
              </div>

              <div className={styles.detailBlock}>
                <span className={styles.detailTitle}>Short Explanation</span>
                <p className={styles.text}>{selectedAnchor.short_explanation}</p>
              </div>

              <div className={styles.detailBlock}>
                <span className={styles.detailTitle}>Long Explanation</span>
                <p className={styles.text}>{selectedAnchor.long_explanation}</p>
              </div>

              <div className={styles.detailBlock}>
                <span className={styles.detailTitle}>Prerequisite</span>
                <p className={styles.text}>{selectedAnchor.prerequisite || "없음"}</p>
              </div>

              {relatedPages.length > 0 ? (
                <div className={styles.detailBlock}>
                  <span className={styles.detailTitle}>Related Pages</span>
                  <div className={styles.relatedPages}>
                    {relatedPages.map((pageNumber) => (
                      <button
                        key={pageNumber}
                        type="button"
                        className={styles.relatedPageButton}
                        onClick={() => onNavigateToRelatedPage(pageNumber, selectedAnchor.anchor_id)}
                        disabled={isPageLoading}
                      >
                        p. {pageNumber}
                      </button>
                    ))}
                  </div>
                </div>
              ) : null}

              {confidenceText ? (
                <div className={styles.detailBlock}>
                  <span className={styles.detailTitle}>Confidence</span>
                  <p className={styles.text}>{confidenceText}</p>
                </div>
              ) : null}
            </div>
          ) : (
            <div className={styles.placeholder}>해설 포인트를 클릭하면 여기 표시돼.</div>
          )}
        </section>
      ) : null}
    </aside>
  );
}
