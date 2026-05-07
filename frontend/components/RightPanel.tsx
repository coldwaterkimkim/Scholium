import type { LegacyPrecomputedAnchor } from "@/lib/api";

import styles from "./RightPanel.module.css";

// Legacy/debug-only panel for the old precomputed anchor-click viewer.
// Selected-region explanations now render through SelectedExplanationPanel.
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
  availableLegacyRegionCount: number;
  legacySelectedRegion: LegacyPrecomputedAnchor | null;
  onNavigateToRelatedPage: (pageNumber: number, legacyAnchorId: string) => void;
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
  availableLegacyRegionCount,
  legacySelectedRegion,
  onNavigateToRelatedPage,
}: RightPanelProps) {
  const relatedPages =
    legacySelectedRegion?.related_pages.filter((pageNumber) => pageNumber !== currentPage) ?? [];
  const showLegacyRegionDetailSection = availableLegacyRegionCount > 0;
  const confidenceText =
    legacySelectedRegion && Number.isFinite(legacySelectedRegion.confidence)
      ? `${Math.round(legacySelectedRegion.confidence * 100)}%`
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

      {showLegacyRegionDetailSection ? (
        <section className={styles.section}>
          <span className={styles.label}>Legacy Region Details</span>
          {legacySelectedRegion ? (
            <div className={styles.anchorDetails}>
              <div className={styles.detailBlock}>
                <span className={styles.detailTitle}>Label</span>
                <p className={styles.text}>{legacySelectedRegion.label}</p>
              </div>

              <div className={styles.detailBlock}>
                <span className={styles.detailTitle}>Question</span>
                <p className={styles.text}>{legacySelectedRegion.question}</p>
              </div>

              <div className={styles.detailBlock}>
                <span className={styles.detailTitle}>Short Explanation</span>
                <p className={styles.text}>{legacySelectedRegion.short_explanation}</p>
              </div>

              <div className={styles.detailBlock}>
                <span className={styles.detailTitle}>Long Explanation</span>
                <p className={styles.text}>{legacySelectedRegion.long_explanation}</p>
              </div>

              <div className={styles.detailBlock}>
                <span className={styles.detailTitle}>Prerequisite</span>
                <p className={styles.text}>{legacySelectedRegion.prerequisite || "없음"}</p>
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
                        onClick={() => onNavigateToRelatedPage(pageNumber, legacySelectedRegion.anchor_id)}
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
