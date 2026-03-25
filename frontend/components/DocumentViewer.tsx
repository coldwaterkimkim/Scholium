"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  ApiRequestError,
  type DocumentMeta,
  type DocumentSummary,
  type FinalAnchor,
  type InteractionLogPayload,
  type PageData,
  getDocument,
  getDocumentSummary,
  getPageResult,
  postInteractionLog,
} from "@/lib/api";
import type { ImageDisplayMetrics } from "@/utils/bbox";

import { AnchorOverlay } from "./AnchorOverlay";
import { RightPanel } from "./RightPanel";
import styles from "./DocumentViewer.module.css";

type DocumentViewerProps = {
  documentId: string;
};

type LoadingState = {
  document: boolean;
  page: boolean;
};

type ErrorState = {
  document: string | null;
  page: string | null;
};

function getErrorMessage(error: unknown, fallbackMessage: string): string {
  if (error instanceof ApiRequestError) {
    return error.message;
  }

  if (error instanceof Error && error.message) {
    return error.message;
  }

  return fallbackMessage;
}

export function DocumentViewer({ documentId }: DocumentViewerProps) {
  const [currentPage, setCurrentPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [documentMeta, setDocumentMeta] = useState<DocumentMeta | null>(null);
  const [documentSummary, setDocumentSummary] = useState<DocumentSummary | null>(null);
  const [currentPageData, setCurrentPageData] = useState<PageData | null>(null);
  const [selectedAnchorId, setSelectedAnchorId] = useState<string | null>(null);
  const [imageDisplayMetrics, setImageDisplayMetrics] = useState<ImageDisplayMetrics | null>(null);
  const [loading, setLoading] = useState<LoadingState>({ document: true, page: false });
  const [error, setError] = useState<ErrorState>({ document: null, page: null });
  const [summaryError, setSummaryError] = useState<string | null>(null);

  const documentRequestIdRef = useRef(0);
  const pageRequestIdRef = useRef(0);
  const initialPageLoadRef = useRef(true);
  const viewerCanvasRef = useRef<HTMLDivElement | null>(null);
  const imageRef = useRef<HTMLImageElement | null>(null);
  const loggedPageViewKeyRef = useRef<string | null>(null);

  const currentAnchors = currentPageData?.final_anchors ?? [];
  const selectedAnchor = useMemo<FinalAnchor | null>(() => {
    if (!selectedAnchorId) {
      return null;
    }

    return currentAnchors.find((anchor) => anchor.anchor_id === selectedAnchorId) ?? null;
  }, [currentAnchors, selectedAnchorId]);

  const updateImageDisplayMetrics = useCallback(() => {
    const wrapper = viewerCanvasRef.current;
    const image = imageRef.current;

    if (!wrapper || !image) {
      setImageDisplayMetrics(null);
      return;
    }

    const wrapperRect = wrapper.getBoundingClientRect();
    const imageRect = image.getBoundingClientRect();

    if (imageRect.width <= 0 || imageRect.height <= 0 || wrapperRect.width <= 0 || wrapperRect.height <= 0) {
      setImageDisplayMetrics(null);
      return;
    }

    setImageDisplayMetrics({
      width: imageRect.width,
      height: imageRect.height,
      offsetLeft: Math.max(0, imageRect.left - wrapperRect.left),
      offsetTop: Math.max(0, imageRect.top - wrapperRect.top),
    });
  }, []);

  const dispatchInteractionLog = useCallback((payload: InteractionLogPayload) => {
    void postInteractionLog(payload).catch(() => {});
  }, []);

  const navigateToPage = useCallback(
    (pageNumber: number) => {
      if (pageNumber < 1 || pageNumber > totalPages || pageNumber === currentPage || loading.page) {
        return;
      }

      setSelectedAnchorId(null);
      setImageDisplayMetrics(null);
      setCurrentPage(pageNumber);
    },
    [currentPage, loading.page, totalPages],
  );

  const handleAnchorSelect = useCallback(
    (anchorId: string) => {
      setSelectedAnchorId(anchorId);

      if (!currentPageData) {
        return;
      }

      dispatchInteractionLog({
        document_id: currentPageData.document_id,
        page_number: currentPageData.page_number,
        anchor_id: anchorId,
        event_type: "anchor_click",
      });
    },
    [currentPageData, dispatchInteractionLog],
  );

  const handleRelatedPageNavigate = useCallback(
    (pageNumber: number, anchorId: string) => {
      if (!currentPageData || loading.page) {
        return;
      }

      dispatchInteractionLog({
        document_id: currentPageData.document_id,
        page_number: currentPageData.page_number,
        anchor_id: anchorId,
        event_type: "related_page_jump",
      });

      navigateToPage(pageNumber);
    },
    [currentPageData, dispatchInteractionLog, loading.page, navigateToPage],
  );

  const handlePageImageLoad = useCallback(() => {
    updateImageDisplayMetrics();

    if (!currentPageData) {
      return;
    }

    const pageViewKey = `${currentPageData.document_id}:${currentPageData.page_number}:${currentPageData.image_url}`;
    if (loggedPageViewKeyRef.current === pageViewKey) {
      return;
    }

    loggedPageViewKeyRef.current = pageViewKey;
    dispatchInteractionLog({
      document_id: currentPageData.document_id,
      page_number: currentPageData.page_number,
      anchor_id: null,
      event_type: "page_view",
    });
  }, [currentPageData, dispatchInteractionLog, updateImageDisplayMetrics]);

  useEffect(() => {
    const documentRequestId = ++documentRequestIdRef.current;
    const summaryController = new AbortController();
    const documentController = new AbortController();

    setCurrentPage(1);
    setTotalPages(1);
    setDocumentMeta(null);
    setDocumentSummary(null);
    setCurrentPageData(null);
    setSelectedAnchorId(null);
    setImageDisplayMetrics(null);
    loggedPageViewKeyRef.current = null;
    setSummaryError(null);
    setError({ document: null, page: null });
    setLoading({ document: true, page: false });
    initialPageLoadRef.current = true;

    async function loadDocumentMetaAndSummary() {
      try {
        const meta = await getDocument(documentId, documentController.signal);
        if (documentRequestId !== documentRequestIdRef.current) {
          return;
        }

        setDocumentMeta(meta);
        setTotalPages(meta.total_pages ?? 1);
        setCurrentPage(1);
        setError((previous) => ({ ...previous, document: null }));
        setLoading((previous) => ({ ...previous, page: true }));

        getDocumentSummary(documentId, summaryController.signal)
          .then((summary) => {
            if (documentRequestId !== documentRequestIdRef.current) {
              return;
            }

            setDocumentSummary(summary);
            setSummaryError(null);
          })
          .catch((summaryFetchError: unknown) => {
            if (documentRequestId !== documentRequestIdRef.current) {
              return;
            }

            setDocumentSummary(null);
            setSummaryError(getErrorMessage(summaryFetchError, "문서 요약을 불러올 수 없어."));
          })
          .finally(() => {
            if (documentRequestId !== documentRequestIdRef.current) {
              return;
            }

            setLoading((previous) => ({ ...previous, document: false }));
          });
      } catch (documentFetchError: unknown) {
        if (documentRequestId !== documentRequestIdRef.current) {
          return;
        }

        setError({
          document: getErrorMessage(documentFetchError, "문서를 불러올 수 없어."),
          page: null,
        });
        setLoading({ document: false, page: false });
      }
    }

    void loadDocumentMetaAndSummary();

    return () => {
      documentController.abort();
      summaryController.abort();
    };
  }, [documentId]);

  useEffect(() => {
    setSelectedAnchorId(null);
    setImageDisplayMetrics(null);
    loggedPageViewKeyRef.current = null;
  }, [currentPage, documentId]);

  useEffect(() => {
    if (!documentMeta) {
      return;
    }

    const pageRequestId = ++pageRequestIdRef.current;
    const pageController = new AbortController();

    setLoading((previous) => ({ ...previous, page: true }));
    setError((previous) => ({ ...previous, page: null }));
    setCurrentPageData(null);
    loggedPageViewKeyRef.current = null;

    async function loadPage() {
      try {
        const pageData = await getPageResult(documentId, currentPage, pageController.signal);
        if (pageRequestId !== pageRequestIdRef.current) {
          return;
        }

        setCurrentPageData(pageData);
        setError((previous) => ({ ...previous, page: null }));
      } catch (pageFetchError: unknown) {
        if (pageRequestId !== pageRequestIdRef.current) {
          return;
        }

        const pageMessage = getErrorMessage(pageFetchError, "페이지 결과를 불러올 수 없어.");
        setCurrentPageData(null);
        setSelectedAnchorId(null);
        setImageDisplayMetrics(null);
        loggedPageViewKeyRef.current = null;
        if (currentPage === 1 && initialPageLoadRef.current) {
          setError({
            document: pageMessage,
            page: null,
          });
        } else {
          setError((previous) => ({
            ...previous,
            page: pageMessage,
          }));
        }
      } finally {
        if (pageRequestId !== pageRequestIdRef.current) {
          return;
        }

        if (currentPage === 1 && initialPageLoadRef.current) {
          initialPageLoadRef.current = false;
        }
        setLoading((previous) => ({ ...previous, page: false }));
      }
    }

    void loadPage();

    return () => {
      pageController.abort();
    };
  }, [currentPage, documentId, documentMeta]);

  useEffect(() => {
    if (!currentPageData) {
      return;
    }

    if (selectedAnchorId && !currentAnchors.some((anchor) => anchor.anchor_id === selectedAnchorId)) {
      setSelectedAnchorId(null);
    }
  }, [currentAnchors, currentPageData, selectedAnchorId]);

  useEffect(() => {
    if (!currentPageData) {
      setImageDisplayMetrics(null);
      return;
    }

    const wrapper = viewerCanvasRef.current;
    const image = imageRef.current;

    if (!wrapper || !image) {
      return;
    }

    updateImageDisplayMetrics();

    if (typeof ResizeObserver === "undefined") {
      return;
    }

    const resizeObserver = new ResizeObserver(() => {
      updateImageDisplayMetrics();
    });

    resizeObserver.observe(wrapper);
    resizeObserver.observe(image);

    return () => {
      resizeObserver.disconnect();
    };
  }, [currentPageData, updateImageDisplayMetrics]);

  const documentTitle = useMemo(() => {
    return documentSummary?.overall_topic || documentMeta?.filename || "문서 viewer";
  }, [documentMeta?.filename, documentSummary?.overall_topic]);

  const canGoPrevious = currentPage > 1 && !loading.page;
  const canGoNext = currentPage < totalPages && !loading.page;

  if (error.document) {
    return (
      <div className={styles.page}>
        <div className={styles.documentErrorShell}>
          <div className={styles.documentErrorBox}>
            <h1 className={styles.documentErrorTitle}>문서를 불러올 수 없어.</h1>
            <p className={styles.documentErrorText}>{error.document}</p>
          </div>
        </div>
      </div>
    );
  }

  const showInitialLoading = !documentMeta && loading.document;

  if (showInitialLoading) {
    return (
      <div className={styles.page}>
        <div className={styles.documentErrorShell}>
          <div className={styles.documentErrorBox}>
            <h1 className={styles.documentErrorTitle}>문서를 불러오는 중...</h1>
            <p className={styles.documentErrorText}>문서 메타데이터를 먼저 확인하고 있어.</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className={styles.page}>
      <div className={styles.shell}>
        <div className={styles.mainColumn}>
          <section className={`${styles.surface} ${styles.topBar}`}>
            <div className={styles.topBarMeta}>
              <div className={styles.filename}>{documentMeta?.filename ?? documentId}</div>
              <div className={styles.metaRow}>
                <span>status: {documentMeta?.status ?? "-"}</span>
                <span>
                  페이지 {currentPage} / {totalPages}
                </span>
              </div>
            </div>

            <div className={styles.navRow}>
              <button
                type="button"
                className={styles.navButton}
                onClick={() => navigateToPage(currentPage - 1)}
                disabled={!canGoPrevious}
              >
                이전
              </button>
              <div className={styles.pageIndicator}>
                {currentPage} / {totalPages}
              </div>
              <button
                type="button"
                className={styles.navButton}
                onClick={() => navigateToPage(currentPage + 1)}
                disabled={!canGoNext}
              >
                다음
              </button>
            </div>
          </section>

          <section className={`${styles.surface} ${styles.viewerSurface}`}>
            <div className={styles.viewerFrame}>
              {loading.page ? (
                <div className={styles.stateBlock}>페이지를 불러오는 중...</div>
              ) : error.page ? (
                <div className={`${styles.stateBlock} ${styles.pageError}`}>
                  <div>
                    <div>{error.page}</div>
                    <div className={styles.pageHint}>이전/다음 버튼으로 다른 페이지는 계속 확인할 수 있어.</div>
                  </div>
                </div>
              ) : currentPageData ? (
                <div ref={viewerCanvasRef} className={styles.viewerCanvas}>
                  <img
                    ref={imageRef}
                    alt={`${documentMeta?.filename ?? documentId} ${currentPage}페이지`}
                    className={styles.pageImage}
                    src={currentPageData.image_url}
                    onLoad={handlePageImageLoad}
                    onError={() => {
                      setImageDisplayMetrics(null);
                      loggedPageViewKeyRef.current = null;
                    }}
                  />
                  <AnchorOverlay
                    anchors={currentAnchors}
                    imageDisplayMetrics={imageDisplayMetrics}
                    selectedAnchorId={selectedAnchorId}
                    onSelectAnchor={handleAnchorSelect}
                  />
                </div>
              ) : (
                <div className={styles.stateBlock}>페이지 데이터를 아직 표시할 수 없어.</div>
              )}
            </div>
          </section>
        </div>

        <RightPanel
          documentTitle={documentTitle}
          filename={documentMeta?.filename ?? null}
          isSummaryLoading={loading.document}
          summaryError={summaryError}
          pageRole={currentPageData?.page_role ?? null}
          pageSummary={currentPageData?.page_summary ?? null}
          pageRiskNote={currentPageData?.page_risk_note ?? null}
          isPageLoading={loading.page}
          pageError={error.page}
          currentPage={currentPage}
          availableAnchorCount={currentAnchors.length}
          selectedAnchor={selectedAnchor}
          onNavigateToRelatedPage={handleRelatedPageNavigate}
        />
      </div>
    </div>
  );
}
