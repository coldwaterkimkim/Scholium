"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";

import {
  ApiRequestError,
  type DocumentMeta,
  type DocumentSummary,
  type InteractionLogPayload,
  type PageData,
  type PageElement,
  type RelatedConceptPage,
  type SelectionExplanation,
  type SelectionExplanationHistoryItem,
  createSelectionExplanation,
  deleteSelectionExplanation,
  getDocument,
  getDocumentSummary,
  getPageResult,
  getSelectionExplanationHistory,
  postInteractionLog,
  updateSelectionExplanationState,
} from "@/lib/api";
import {
  normalizedBboxToPixelRect,
  type ImageDisplayMetrics,
  type NormalizedBBox,
  type PixelRect,
} from "@/utils/bbox";

import { SelectedExplanationPanel } from "./SelectedExplanationPanel";
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

type DragSelection = {
  startX: number;
  startY: number;
  currentX: number;
  currentY: number;
  isDragging: boolean;
};

type ViewerNotice = {
  left: number;
  top: number;
  message: string;
  tone: "loading" | "error";
} | null;

type SelectionJobStatus = "pending" | "ready" | "error";

type SelectionJob = {
  id: string;
  pageNumber: number;
  bbox: NormalizedBBox;
  status: SelectionJobStatus;
  createdAt: number;
  autoOpen: boolean;
  isImportant?: boolean;
  explanation?: SelectionExplanation;
  errorMessage?: string;
};

type RelatedFocusTarget = {
  pageNumber: number;
  concept: string;
  relationReason: string;
  sourceId: string;
  requestKey: string;
};

type RelatedFocus = {
  bbox: NormalizedBBox;
  concept: string;
  relationReason: string;
  key: string;
};

type PanelPlacement = {
  panelStyle: {
    left: number;
    top: number;
    width: number;
  };
  connectorLine: {
    x1: number;
    y1: number;
    x2: number;
    y2: number;
  };
  canvasWidth: number;
  canvasHeight: number;
} | null;

type ChipContextMenu = {
  jobId: string;
  left: number;
  top: number;
} | null;

const RELATED_FOCUS_ALIASES: Array<[RegExp, string]> = [
  [/전도대/, "conduction band"],
  [/가전자대/, "valence band"],
  [/밴드갭|에너지\s*갭/, "energy gap band gap eg"],
  [/에너지\s*밴드/, "energy band"],
  [/결합성/, "bonding orbital"],
  [/반결합성/, "antibonding orbital"],
  [/페르미/, "fermi"],
  [/상태밀도/, "density of states"],
  [/유효질량/, "effective mass"],
];

function clamp(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) {
    return min;
  }

  return Math.min(Math.max(value, min), max);
}

function normalizePixelRect(rect: DragSelection): PixelRect {
  const left = Math.min(rect.startX, rect.currentX);
  const top = Math.min(rect.startY, rect.currentY);
  const width = Math.abs(rect.currentX - rect.startX);
  const height = Math.abs(rect.currentY - rect.startY);
  return { left, top, width, height };
}

function rectArea(rect: PixelRect): number {
  return Math.max(0, rect.width) * Math.max(0, rect.height);
}

function isPointInsideImage(point: { x: number; y: number }, imageDisplayMetrics: ImageDisplayMetrics): boolean {
  return (
    point.x >= imageDisplayMetrics.offsetLeft &&
    point.x <= imageDisplayMetrics.offsetLeft + imageDisplayMetrics.width &&
    point.y >= imageDisplayMetrics.offsetTop &&
    point.y <= imageDisplayMetrics.offsetTop + imageDisplayMetrics.height
  );
}

function pixelRectToNormalizedBbox(
  selectionRect: PixelRect,
  imageDisplayMetrics: ImageDisplayMetrics,
): NormalizedBBox | null {
  const imageLeft = imageDisplayMetrics.offsetLeft;
  const imageTop = imageDisplayMetrics.offsetTop;
  const imageRight = imageLeft + imageDisplayMetrics.width;
  const imageBottom = imageTop + imageDisplayMetrics.height;

  const left = clamp(selectionRect.left, imageLeft, imageRight);
  const top = clamp(selectionRect.top, imageTop, imageBottom);
  const right = clamp(selectionRect.left + selectionRect.width, imageLeft, imageRight);
  const bottom = clamp(selectionRect.top + selectionRect.height, imageTop, imageBottom);
  const width = right - left;
  const height = bottom - top;

  if (width < 4 || height < 4) {
    return null;
  }

  const round = (value: number) => Math.round(value * 10_000) / 10_000;
  return [
    round((left - imageLeft) / imageDisplayMetrics.width),
    round((top - imageTop) / imageDisplayMetrics.height),
    round(width / imageDisplayMetrics.width),
    round(height / imageDisplayMetrics.height),
  ];
}

function buildPanelPlacement(
  selectedAnchorRect: PixelRect | null,
  imageDisplayMetrics: ImageDisplayMetrics | null,
): PanelPlacement {
  if (!selectedAnchorRect || !imageDisplayMetrics) {
    return null;
  }

  const canvasWidth = Math.max(1, imageDisplayMetrics.offsetLeft + imageDisplayMetrics.width);
  const canvasHeight = Math.max(1, imageDisplayMetrics.offsetTop + imageDisplayMetrics.height);
  const panelWidth = Math.round(clamp(canvasWidth * 0.42, 340, 460));
  const panelHeightEstimate = 620;
  const gap = 28;
  const rightCandidate = selectedAnchorRect.left + selectedAnchorRect.width + gap;
  const hasRoomRight = rightCandidate + panelWidth + 12 <= canvasWidth;
  const leftCandidate = selectedAnchorRect.left - panelWidth - gap;
  const rawLeft = hasRoomRight ? rightCandidate : leftCandidate;
  const maxLeft = Math.max(12, canvasWidth - panelWidth - 12);
  const panelLeft = clamp(rawLeft, 12, maxLeft);
  const maxTop = Math.max(12, canvasHeight - Math.min(panelHeightEstimate, canvasHeight - 24) - 12);
  const panelTop = clamp(selectedAnchorRect.top - 96, 12, maxTop);
  const anchorIsLeftOfPanel = selectedAnchorRect.left + selectedAnchorRect.width / 2 < panelLeft + panelWidth / 2;
  const x1 = anchorIsLeftOfPanel
    ? selectedAnchorRect.left + selectedAnchorRect.width
    : selectedAnchorRect.left;
  const y1 = selectedAnchorRect.top + selectedAnchorRect.height / 2;
  const x2 = anchorIsLeftOfPanel ? panelLeft : panelLeft + panelWidth;
  const y2 = clamp(y1, panelTop + 82, panelTop + panelHeightEstimate - 82);

  return {
    panelStyle: {
      left: panelLeft,
      top: panelTop,
      width: panelWidth,
    },
    connectorLine: { x1, y1, x2, y2 },
    canvasWidth,
    canvasHeight,
  };
}

function getErrorMessage(error: unknown, fallbackMessage: string): string {
  if (error instanceof ApiRequestError) {
    return error.message;
  }

  if (error instanceof Error && error.message) {
    return error.message;
  }

  return fallbackMessage;
}

function normalizeSearchText(value: string): string {
  return value
    .normalize("NFKC")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s]/gu, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function expandSearchText(value: string): string {
  const aliases = RELATED_FOCUS_ALIASES.flatMap(([pattern, alias]) => (pattern.test(value) ? [alias] : []));
  return aliases.length > 0 ? `${value} ${aliases.join(" ")}` : value;
}

function tokenizeSearchText(value: string): string[] {
  return normalizeSearchText(value)
    .split(" ")
    .map((token) => token.trim())
    .filter((token) => token.length >= 2);
}

function scoreRelatedElementMatch(element: PageElement, target: RelatedFocusTarget): number {
  const conceptText = normalizeSearchText(expandSearchText(target.concept));
  const targetText = normalizeSearchText(expandSearchText(`${target.concept} ${target.relationReason}`));
  const elementLabel = normalizeSearchText(element.label);
  const elementText = normalizeSearchText(
    `${element.label} ${element.question} ${element.short_explanation}`,
  );

  let score = 0;
  if (conceptText && elementText.includes(conceptText)) {
    score += 5;
  }
  if (targetText && elementText.includes(targetText)) {
    score += 4;
  }
  if (elementLabel && (conceptText.includes(elementLabel) || targetText.includes(elementLabel))) {
    score += 3;
  }
  if (elementLabel && conceptText && (elementLabel.includes(conceptText) || conceptText.includes(elementLabel))) {
    score += 2;
  }

  const conceptTokens = tokenizeSearchText(conceptText);
  if (conceptTokens.length > 0) {
    const matchedConceptTokens = conceptTokens.filter((token) => elementText.includes(token));
    score += (matchedConceptTokens.length / conceptTokens.length) * 2.5;
  }

  const targetTokens = tokenizeSearchText(targetText);
  if (targetTokens.length > 0) {
    const matchedTokens = targetTokens.filter((token) => elementText.includes(token));
    score += matchedTokens.length / targetTokens.length;
  }

  return score + element.confidence * 0.15;
}

function findRelatedFocusElement(elements: PageElement[], target: RelatedFocusTarget): PageElement | null {
  if (elements.length === 0) {
    return null;
  }

  const ranked = elements
    .map((element) => ({ element, score: scoreRelatedElementMatch(element, target) }))
    .sort((left, right) => right.score - left.score);

  const bestMatch = ranked[0];
  if (!bestMatch || bestMatch.score < 0.3) {
    return null;
  }

  return bestMatch.element;
}

function buildSelectionJobId(pageNumber: number, bbox: NormalizedBBox, sequence: number): string {
  const bboxKey = bbox.map((value) => Math.round(value * 10_000)).join("-");
  return `selection-${pageNumber}-${bboxKey}-${sequence}`;
}

function buildSelectionChipLabel(job: SelectionJob): string {
  if (job.status === "pending") {
    return "Generating";
  }
  if (job.status === "error") {
    return "Failed";
  }
  return "Ready";
}

function buildSelectionChipHoverLabel(job: SelectionJob): string {
  if (job.status === "pending") {
    return "설명 생성 중";
  }
  if (job.status === "error") {
    return "생성 실패";
  }

  return job.explanation?.concept_title || job.explanation?.label || "선택 설명";
}

function buildSelectionChipTitle(job: SelectionJob): string {
  if (job.status === "pending") {
    return "설명을 생성하는 중";
  }
  if (job.status === "error") {
    return job.errorMessage ? `생성 실패: ${job.errorMessage}` : "생성 실패";
  }

  const title = job.explanation?.concept_title || job.explanation?.label || "선택 설명";
  return `${title} 다시 열기`;
}

function buildSelectionHistoryJob(item: SelectionExplanationHistoryItem): SelectionJob {
  const { explanation } = item;
  return {
    id: `history-${explanation.selection_id}`,
    pageNumber: explanation.page_number,
    bbox: explanation.selected_bbox,
    status: "ready",
    createdAt: Date.now(),
    autoOpen: false,
    isImportant: item.is_important,
    explanation,
  };
}

function isSamePersistedSelection(job: SelectionJob, explanation: SelectionExplanation): boolean {
  return (
    job.explanation?.selection_id === explanation.selection_id ||
    job.id === `history-${explanation.selection_id}` ||
    (job.pageNumber === explanation.page_number &&
      job.bbox.every((value, index) => Math.abs(value - explanation.selected_bbox[index]) < 0.0001))
  );
}

export function DocumentViewer({ documentId }: DocumentViewerProps) {
  const [currentPage, setCurrentPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [documentMeta, setDocumentMeta] = useState<DocumentMeta | null>(null);
  const [documentSummary, setDocumentSummary] = useState<DocumentSummary | null>(null);
  const [currentPageData, setCurrentPageData] = useState<PageData | null>(null);
  const [selectionJobs, setSelectionJobs] = useState<SelectionJob[]>([]);
  const [activeSelectionJobId, setActiveSelectionJobId] = useState<string | null>(null);
  const [imageDisplayMetrics, setImageDisplayMetrics] = useState<ImageDisplayMetrics | null>(null);
  const [dragSelection, setDragSelection] = useState<DragSelection | null>(null);
  const [viewerNotice, setViewerNotice] = useState<ViewerNotice>(null);
  const [chipContextMenu, setChipContextMenu] = useState<ChipContextMenu>(null);
  const [pendingRelatedFocus, setPendingRelatedFocus] = useState<RelatedFocusTarget | null>(null);
  const [relatedFocus, setRelatedFocus] = useState<RelatedFocus | null>(null);
  const [loading, setLoading] = useState<LoadingState>({ document: true, page: false });
  const [error, setError] = useState<ErrorState>({ document: null, page: null });
  const [summaryError, setSummaryError] = useState<string | null>(null);

  const documentRequestIdRef = useRef(0);
  const pageRequestIdRef = useRef(0);
  const selectionHistoryRequestIdRef = useRef(0);
  const initialPageLoadRef = useRef(true);
  const viewerCanvasRef = useRef<HTMLDivElement | null>(null);
  const imageRef = useRef<HTMLImageElement | null>(null);
  const loggedPageViewKeyRef = useRef<string | null>(null);
  const dragSelectionRef = useRef<DragSelection | null>(null);
  const activeSelectionJobIdRef = useRef<string | null>(null);
  const selectionJobSequenceRef = useRef(0);
  const selectionJobControllersRef = useRef<Map<string, AbortController>>(new Map());

  const activeSelectionJob = useMemo(() => {
    if (!activeSelectionJobId) {
      return null;
    }

    return selectionJobs.find((job) => job.id === activeSelectionJobId) ?? null;
  }, [activeSelectionJobId, selectionJobs]);
  const selectedBbox = activeSelectionJob?.bbox ?? null;
  const selectedExplanation =
    activeSelectionJob?.status === "ready" ? activeSelectionJob.explanation ?? null : null;

  const selectedRegionRect = useMemo<PixelRect | null>(() => {
    if (!selectedBbox || !imageDisplayMetrics) {
      return null;
    }

    return normalizedBboxToPixelRect(selectedBbox, imageDisplayMetrics);
  }, [imageDisplayMetrics, selectedBbox]);
  const panelPlacement = useMemo(
    () => buildPanelPlacement(selectedRegionRect, imageDisplayMetrics),
    [imageDisplayMetrics, selectedRegionRect],
  );
  const visibleDragRect = dragSelection ? normalizePixelRect(dragSelection) : null;
  const relatedFocusRect = useMemo<PixelRect | null>(() => {
    if (!relatedFocus || !imageDisplayMetrics) {
      return null;
    }

    return normalizedBboxToPixelRect(relatedFocus.bbox, imageDisplayMetrics);
  }, [imageDisplayMetrics, relatedFocus]);
  const currentPageSelectionChips = useMemo(() => {
    if (!currentPageData || !imageDisplayMetrics) {
      return [];
    }

    return selectionJobs
      .filter(
        (job) =>
          job.pageNumber === currentPageData.page_number &&
          job.id !== activeSelectionJobId,
      )
      .map((job) => ({
        job,
        rect: normalizedBboxToPixelRect(job.bbox, imageDisplayMetrics),
      }))
      .filter((item): item is { job: SelectionJob; rect: PixelRect } => item.rect !== null)
      .map(({ job, rect }) => {
        const maxLeft = Math.max(8, imageDisplayMetrics.offsetLeft + imageDisplayMetrics.width - 228);
        const maxTop = Math.max(8, imageDisplayMetrics.offsetTop + imageDisplayMetrics.height - 30);
        return {
          job,
          rect,
          left: clamp(rect.left + rect.width - 18, 8, maxLeft),
          top: clamp(rect.top - 13, 8, maxTop),
        };
      });
  }, [activeSelectionJobId, currentPageData, imageDisplayMetrics, selectionJobs]);
  const chipContextMenuJob = useMemo(() => {
    if (!chipContextMenu) {
      return null;
    }

    return selectionJobs.find((job) => job.id === chipContextMenu.jobId) ?? null;
  }, [chipContextMenu, selectionJobs]);

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

  useEffect(() => {
    activeSelectionJobIdRef.current = activeSelectionJobId;
  }, [activeSelectionJobId]);

  useEffect(() => {
    return () => {
      selectionJobControllersRef.current.forEach((controller) => controller.abort());
      selectionJobControllersRef.current.clear();
    };
  }, []);

  const closeActiveSelection = useCallback(() => {
    dragSelectionRef.current = null;
    setDragSelection(null);
    setChipContextMenu(null);
    activeSelectionJobIdRef.current = null;
    setActiveSelectionJobId(null);
  }, []);

  const cancelActiveSelection = useCallback(() => {
    const activeJobId = activeSelectionJobIdRef.current;
    dragSelectionRef.current = null;
    setDragSelection(null);
    setChipContextMenu(null);

    if (activeJobId) {
      selectionJobControllersRef.current.get(activeJobId)?.abort();
      selectionJobControllersRef.current.delete(activeJobId);
      setSelectionJobs((previous) =>
        previous.filter((job) => job.id !== activeJobId || job.status === "ready"),
      );
    }

    activeSelectionJobIdRef.current = null;
    setActiveSelectionJobId(null);
  }, []);

  const resetSelectionJobs = useCallback(() => {
    selectionJobControllersRef.current.forEach((controller) => controller.abort());
    selectionJobControllersRef.current.clear();
    selectionJobSequenceRef.current = 0;
    dragSelectionRef.current = null;
    setDragSelection(null);
    setChipContextMenu(null);
    activeSelectionJobIdRef.current = null;
    setActiveSelectionJobId(null);
    setSelectionJobs([]);
  }, []);

  const navigateToPage = useCallback(
    (pageNumber: number) => {
      if (pageNumber < 1 || pageNumber > totalPages || pageNumber === currentPage || loading.page) {
        return;
      }

      closeActiveSelection();
      setRelatedFocus(null);
      setViewerNotice(null);
      setImageDisplayMetrics(null);
      setCurrentPage(pageNumber);
    },
    [closeActiveSelection, currentPage, loading.page, totalPages],
  );

  const handleRelatedPageNavigate = useCallback(
    (item: RelatedConceptPage, sourceId: string) => {
      if (!currentPageData || loading.page) {
        return;
      }
      const pageNumber = item.page_number;
      if (typeof pageNumber !== "number") {
        return;
      }

      dispatchInteractionLog({
        document_id: currentPageData.document_id,
        page_number: currentPageData.page_number,
        anchor_id: sourceId,
        event_type: "related_page_jump",
      });

      setPendingRelatedFocus({
        pageNumber,
        concept: item.concept,
        relationReason: item.relation_reason,
        sourceId,
        requestKey: `${sourceId}:${pageNumber}:${item.concept}:${Date.now()}`,
      });
      navigateToPage(pageNumber);
    },
    [currentPageData, dispatchInteractionLog, loading.page, navigateToPage],
  );

  const handleCloseSelectedExplanation = useCallback(() => {
    closeActiveSelection();
  }, [closeActiveSelection]);

  const handleCancelActiveSelection = useCallback(() => {
    cancelActiveSelection();
    setPendingRelatedFocus(null);
    setRelatedFocus(null);
    setViewerNotice(null);
  }, [cancelActiveSelection]);

  const handleSelectionChipOpen = useCallback((job: SelectionJob) => {
    setChipContextMenu(null);
    dragSelectionRef.current = null;
    setDragSelection(null);
    setPendingRelatedFocus(null);
    setRelatedFocus(null);
    setViewerNotice(null);
    activeSelectionJobIdRef.current = job.id;
    setActiveSelectionJobId(job.id);
  }, []);

  const runSelectionJob = useCallback(
    async (job: SelectionJob, documentIdForJob: string) => {
      const controller = new AbortController();
      selectionJobControllersRef.current.set(job.id, controller);

      dispatchInteractionLog({
        document_id: documentIdForJob,
        page_number: job.pageNumber,
        anchor_id: job.id,
        event_type: "selection_explanation_request",
      });

      try {
        const explanation = await createSelectionExplanation(
          documentIdForJob,
          job.pageNumber,
          job.bbox,
          controller.signal,
        );
        if (controller.signal.aborted) {
          return;
        }

        selectionJobControllersRef.current.delete(job.id);
        setSelectionJobs((previous) =>
          previous.map((previousJob) =>
            previousJob.id === job.id
              ? {
                  ...previousJob,
                  pageNumber: explanation.page_number,
                  bbox: explanation.selected_bbox,
                  status: "ready",
                  explanation,
                  errorMessage: undefined,
                }
              : previousJob,
          ),
        );
        dispatchInteractionLog({
          document_id: documentIdForJob,
          page_number: job.pageNumber,
          anchor_id: explanation.selection_id,
          event_type: "selection_explanation_success",
        });
      } catch (selectionError: unknown) {
        if (controller.signal.aborted) {
          return;
        }

        selectionJobControllersRef.current.delete(job.id);
        const message = getErrorMessage(selectionError, "선택 영역 설명을 생성할 수 없어.");
        setSelectionJobs((previous) =>
          previous.map((previousJob) =>
            previousJob.id === job.id
              ? {
                  ...previousJob,
                  status: "error",
                  errorMessage: message,
                }
              : previousJob,
          ),
        );
        dispatchInteractionLog({
          document_id: documentIdForJob,
          page_number: job.pageNumber,
          anchor_id: job.id,
          event_type: "selection_explanation_failure",
        });
      }
    },
    [dispatchInteractionLog],
  );

  const enqueueSelectionJob = useCallback(
    (normalizedBbox: NormalizedBBox) => {
      if (!currentPageData) {
        return;
      }

      const sequence = ++selectionJobSequenceRef.current;
      const jobId = buildSelectionJobId(currentPageData.page_number, normalizedBbox, sequence);
      const shouldAutoOpen = activeSelectionJobIdRef.current === null;
      const job: SelectionJob = {
        id: jobId,
        pageNumber: currentPageData.page_number,
        bbox: normalizedBbox,
        status: "pending",
        createdAt: Date.now(),
        autoOpen: shouldAutoOpen,
      };

      setSelectionJobs((previous) => [...previous, job].slice(-32));
      if (shouldAutoOpen) {
        activeSelectionJobIdRef.current = jobId;
        setActiveSelectionJobId(jobId);
      }

      void runSelectionJob(job, currentPageData.document_id);
    },
    [currentPageData, runSelectionJob],
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

  const getCanvasPointFromClient = useCallback((clientX: number, clientY: number) => {
    const canvas = viewerCanvasRef.current;
    if (!canvas) {
      return null;
    }

    const rect = canvas.getBoundingClientRect();
    return {
      x: clientX - rect.left,
      y: clientY - rect.top,
    };
  }, []);

  const getCanvasPoint = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => getCanvasPointFromClient(event.clientX, event.clientY),
    [getCanvasPointFromClient],
  );

  const handleSelectionChipContextMenu = useCallback(
    (job: SelectionJob, event: ReactMouseEvent<HTMLButtonElement>) => {
      event.preventDefault();
      event.stopPropagation();

      const point = getCanvasPointFromClient(event.clientX, event.clientY);
      const canvas = viewerCanvasRef.current;
      if (!point || !canvas) {
        return;
      }

      setChipContextMenu({
        jobId: job.id,
        left: clamp(point.x, 8, Math.max(8, canvas.clientWidth - 172)),
        top: clamp(point.y, 8, Math.max(8, canvas.clientHeight - 92)),
      });
    },
    [getCanvasPointFromClient],
  );

  const handleToggleSelectionJobImportant = useCallback(
    async (job: SelectionJob) => {
      if (!job.explanation) {
        return;
      }

      setChipContextMenu(null);
      const nextIsImportant = !job.isImportant;
      setSelectionJobs((previous) =>
        previous.map((previousJob) =>
          previousJob.id === job.id ? { ...previousJob, isImportant: nextIsImportant } : previousJob,
        ),
      );

      try {
        const state = await updateSelectionExplanationState(
          job.explanation.document_id,
          job.explanation.page_number,
          job.explanation.selection_id,
          { is_important: nextIsImportant },
        );
        setSelectionJobs((previous) =>
          previous.map((previousJob) =>
            previousJob.explanation?.selection_id === state.selection_id
              ? { ...previousJob, isImportant: state.is_important }
              : previousJob,
          ),
        );
      } catch (importantError: unknown) {
        setSelectionJobs((previous) =>
          previous.map((previousJob) =>
            previousJob.id === job.id ? { ...previousJob, isImportant: Boolean(job.isImportant) } : previousJob,
          ),
        );
        const rect = imageDisplayMetrics ? normalizedBboxToPixelRect(job.bbox, imageDisplayMetrics) : null;
        setViewerNotice({
          left: rect ? Math.max(12, rect.left) : 12,
          top: rect ? Math.max(12, rect.top - 42) : 12,
          message: getErrorMessage(importantError, "중요 표시를 저장할 수 없어."),
          tone: "error",
        });
      }
    },
    [imageDisplayMetrics],
  );

  const handleDeleteSelectionJob = useCallback(
    async (job: SelectionJob) => {
      setChipContextMenu(null);
      const rect = imageDisplayMetrics ? normalizedBboxToPixelRect(job.bbox, imageDisplayMetrics) : null;

      if (job.status === "pending") {
        selectionJobControllersRef.current.get(job.id)?.abort();
        selectionJobControllersRef.current.delete(job.id);
        setSelectionJobs((previous) => previous.filter((previousJob) => previousJob.id !== job.id));
        if (activeSelectionJobIdRef.current === job.id) {
          closeActiveSelection();
        }
        return;
      }

      if (!job.explanation) {
        setSelectionJobs((previous) => previous.filter((previousJob) => previousJob.id !== job.id));
        return;
      }

      try {
        await deleteSelectionExplanation(
          job.explanation.document_id,
          job.explanation.page_number,
          job.explanation.selection_id,
        );
        setSelectionJobs((previous) => previous.filter((previousJob) => previousJob.id !== job.id));
        if (activeSelectionJobIdRef.current === job.id) {
          closeActiveSelection();
        }
      } catch (deleteError: unknown) {
        setViewerNotice({
          left: rect ? Math.max(12, rect.left) : 12,
          top: rect ? Math.max(12, rect.top - 42) : 12,
          message: getErrorMessage(deleteError, "선택 설명을 삭제할 수 없어."),
          tone: "error",
        });
      }
    },
    [closeActiveSelection, imageDisplayMetrics],
  );

  const updateDragSelectionToPoint = useCallback((point: { x: number; y: number }) => {
    setDragSelection((previous) => {
      if (!previous?.isDragging) {
        return previous;
      }
      const nextSelection = {
        ...previous,
        currentX: point.x,
        currentY: point.y,
      };
      dragSelectionRef.current = nextSelection;
      return nextSelection;
    });
  }, []);

  const finishDragSelection = useCallback(
    (point: { x: number; y: number } | null) => {
      const activeDragSelection = dragSelectionRef.current;
      if (!activeDragSelection || !imageDisplayMetrics) {
        return;
      }

      const finalSelection = normalizePixelRect({
        ...activeDragSelection,
        currentX: point?.x ?? activeDragSelection.currentX,
        currentY: point?.y ?? activeDragSelection.currentY,
      });
      dragSelectionRef.current = null;
      setDragSelection(null);

      if (rectArea(finalSelection) < 64) {
        return;
      }

      const normalizedBbox = pixelRectToNormalizedBbox(finalSelection, imageDisplayMetrics);
      if (!normalizedBbox) {
        return;
      }

      enqueueSelectionJob(normalizedBbox);
    },
    [enqueueSelectionJob, imageDisplayMetrics],
  );

  const handleCanvasPointerDown = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      if (!currentPageData || !imageDisplayMetrics || event.button !== 0) {
        return;
      }

      const point = getCanvasPoint(event);
      if (!point || !isPointInsideImage(point, imageDisplayMetrics)) {
        return;
      }

      event.preventDefault();
      event.currentTarget.setPointerCapture(event.pointerId);
      setChipContextMenu(null);
      setPendingRelatedFocus(null);
      setRelatedFocus(null);
      setViewerNotice(null);
      dispatchInteractionLog({
        document_id: currentPageData.document_id,
        page_number: currentPageData.page_number,
        anchor_id: null,
        event_type: "selection_start",
      });
      const nextSelection = {
        startX: point.x,
        startY: point.y,
        currentX: point.x,
        currentY: point.y,
        isDragging: true,
      };
      dragSelectionRef.current = nextSelection;
      setDragSelection(nextSelection);
    },
    [currentPageData, dispatchInteractionLog, getCanvasPoint, imageDisplayMetrics],
  );

  const handleCanvasPointerMove = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      if (!dragSelectionRef.current?.isDragging) {
        return;
      }

      const point = getCanvasPoint(event);
      if (!point) {
        return;
      }

      updateDragSelectionToPoint(point);
    },
    [getCanvasPoint, updateDragSelectionToPoint],
  );

  const handleCanvasPointerUp = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      finishDragSelection(getCanvasPoint(event));
    },
    [finishDragSelection, getCanvasPoint],
  );

  const handleCanvasPointerCancel = useCallback(() => {
    dragSelectionRef.current = null;
    setDragSelection(null);
  }, []);

  useEffect(() => {
    function handleWindowPointerMove(event: PointerEvent) {
      if (!dragSelectionRef.current?.isDragging) {
        return;
      }
      const point = getCanvasPointFromClient(event.clientX, event.clientY);
      if (point) {
        updateDragSelectionToPoint(point);
      }
    }

    function handleWindowPointerUp(event: PointerEvent) {
      if (!dragSelectionRef.current) {
        return;
      }
      finishDragSelection(getCanvasPointFromClient(event.clientX, event.clientY));
    }

    window.addEventListener("pointermove", handleWindowPointerMove);
    window.addEventListener("pointerup", handleWindowPointerUp);
    return () => {
      window.removeEventListener("pointermove", handleWindowPointerMove);
      window.removeEventListener("pointerup", handleWindowPointerUp);
    };
  }, [finishDragSelection, getCanvasPointFromClient, updateDragSelectionToPoint]);

  useEffect(() => {
    const documentRequestId = ++documentRequestIdRef.current;
    const summaryController = new AbortController();
    const documentController = new AbortController();

    setCurrentPage(1);
    setTotalPages(1);
    setDocumentMeta(null);
    setDocumentSummary(null);
    setCurrentPageData(null);
    resetSelectionJobs();
    setImageDisplayMetrics(null);
    setViewerNotice(null);
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
  }, [documentId, resetSelectionJobs]);

  useEffect(() => {
    function handleEscape(event: KeyboardEvent) {
      if (event.key !== "Escape") {
        return;
      }

      const target = event.target;
      if (
        target instanceof HTMLInputElement ||
        target instanceof HTMLTextAreaElement ||
        target instanceof HTMLSelectElement ||
        (target instanceof HTMLElement && target.isContentEditable)
      ) {
        return;
      }

      if (chipContextMenu) {
        event.preventDefault();
        setChipContextMenu(null);
        return;
      }

      const hasActiveSelection = Boolean(dragSelectionRef.current || activeSelectionJobIdRef.current);
      if (!hasActiveSelection) {
        return;
      }

      event.preventDefault();
      handleCancelActiveSelection();
    }

    window.addEventListener("keydown", handleEscape);
    return () => {
      window.removeEventListener("keydown", handleEscape);
    };
  }, [chipContextMenu, handleCancelActiveSelection]);

  useEffect(() => {
    closeActiveSelection();
    setImageDisplayMetrics(null);
    setViewerNotice(null);
    loggedPageViewKeyRef.current = null;
  }, [closeActiveSelection, currentPage, documentId]);

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
        closeActiveSelection();
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
  }, [closeActiveSelection, currentPage, documentId, documentMeta]);

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

  useEffect(() => {
    if (!currentPageData) {
      return;
    }

    const requestId = ++selectionHistoryRequestIdRef.current;
    const controller = new AbortController();

    getSelectionExplanationHistory(
      currentPageData.document_id,
      currentPageData.page_number,
      controller.signal,
    )
      .then((history) => {
        if (requestId !== selectionHistoryRequestIdRef.current) {
          return;
        }

        setSelectionJobs((previous) => {
          const restoredJobs = history.items
            .filter((item) => !previous.some((job) => isSamePersistedSelection(job, item.explanation)))
            .map(buildSelectionHistoryJob);
          if (restoredJobs.length === 0) {
            return previous;
          }
          return [...previous, ...restoredJobs].slice(-32);
        });
      })
      .catch((historyError: unknown) => {
        if (controller.signal.aborted || requestId !== selectionHistoryRequestIdRef.current) {
          return;
        }

        setViewerNotice({
          left: 12,
          top: 12,
          message: getErrorMessage(historyError, "선택 설명 기록을 불러올 수 없어."),
          tone: "error",
        });
      });

    return () => {
      controller.abort();
    };
  }, [currentPageData]);

  useEffect(() => {
    if (!pendingRelatedFocus || !currentPageData || !imageDisplayMetrics) {
      return;
    }
    if (currentPageData.page_number !== pendingRelatedFocus.pageNumber) {
      return;
    }

    const matchedElement = findRelatedFocusElement(currentPageData.page_elements, pendingRelatedFocus);
    setPendingRelatedFocus(null);

    if (!matchedElement) {
      setViewerNotice({
        left: 12,
        top: 12,
        message: "관련 요소 위치를 이 페이지에서 확실히 특정하지는 못했어.",
        tone: "error",
      });
      window.setTimeout(() => {
        setViewerNotice((currentNotice) =>
          currentNotice?.message === "관련 요소 위치를 이 페이지에서 확실히 특정하지는 못했어."
            ? null
            : currentNotice,
        );
      }, 2200);
      return;
    }

    const focusKey = `${pendingRelatedFocus.requestKey}:${matchedElement.anchor_id}`;
    setRelatedFocus({
      bbox: matchedElement.bbox,
      concept: matchedElement.label || pendingRelatedFocus.concept,
      relationReason: pendingRelatedFocus.relationReason,
      key: focusKey,
    });

    window.setTimeout(() => {
      setRelatedFocus((currentFocus) => (currentFocus?.key === focusKey ? null : currentFocus));
    }, 2600);
  }, [currentPageData, imageDisplayMetrics, pendingRelatedFocus]);

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
      <div className={`${styles.shell} ${selectedBbox ? styles.shellWithFloatingPanel : ""}`}>
        <div className={styles.mainColumn}>
          <section className={`${styles.surface} ${styles.topBar}`}>
            <div className={styles.topBarMeta}>
              <div className={styles.filename}>{documentMeta?.filename ?? documentId}</div>
              <div className={styles.metaRow}>
                <span>status: {documentMeta?.status ?? "-"}</span>
                <span>
                  페이지 {currentPage} / {totalPages}
                </span>
                {documentSummary?.overall_topic ? <span>{documentSummary.overall_topic}</span> : null}
                {summaryError ? <span>summary unavailable</span> : null}
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
                <div
                  ref={viewerCanvasRef}
                  className={styles.viewerCanvas}
                  onPointerDown={handleCanvasPointerDown}
                  onPointerMove={handleCanvasPointerMove}
                  onPointerUp={handleCanvasPointerUp}
                  onPointerCancel={handleCanvasPointerCancel}
                >
                  <img
                    ref={imageRef}
                    alt={`${documentMeta?.filename ?? documentId} ${currentPage}페이지`}
                    className={styles.pageImage}
                    draggable={false}
                    src={currentPageData.image_url}
                    onLoad={handlePageImageLoad}
                    onError={() => {
                      setImageDisplayMetrics(null);
                      loggedPageViewKeyRef.current = null;
                    }}
                  />
                  {selectedRegionRect ? (
                    <div
                      className={styles.selectedRegionRect}
                      style={{
                        left: `${selectedRegionRect.left}px`,
                        top: `${selectedRegionRect.top}px`,
                        width: `${selectedRegionRect.width}px`,
                        height: `${selectedRegionRect.height}px`,
                      }}
                    />
                  ) : null}
                  {relatedFocus && relatedFocusRect ? (
                    <div
                      key={relatedFocus.key}
                      className={styles.relatedFocusRect}
                      style={{
                        left: `${relatedFocusRect.left}px`,
                        top: `${relatedFocusRect.top}px`,
                        width: `${relatedFocusRect.width}px`,
                        height: `${relatedFocusRect.height}px`,
                      }}
                    >
                      <span className={styles.relatedFocusLabel}>{relatedFocus.concept}</span>
                    </div>
                  ) : null}
                  {visibleDragRect && visibleDragRect.width >= 4 && visibleDragRect.height >= 4 ? (
                    <div
                      className={styles.dragSelectionRect}
                      style={{
                        left: `${visibleDragRect.left}px`,
                        top: `${visibleDragRect.top}px`,
                        width: `${visibleDragRect.width}px`,
                        height: `${visibleDragRect.height}px`,
                      }}
                    />
                  ) : null}
                  {currentPageSelectionChips.map(({ job, left, top }) => (
                    <button
                      key={job.id}
                      type="button"
                      className={`${styles.annotationChip} ${
                        job.status === "pending"
                          ? styles.annotationChipPending
                          : job.status === "error"
                            ? styles.annotationChipError
                            : styles.annotationChipReady
                      } ${job.isImportant ? styles.annotationChipImportant : ""}`}
                      style={{
                        left: `${left}px`,
                        top: `${top}px`,
                      }}
                      aria-label={buildSelectionChipTitle(job)}
                      title={buildSelectionChipTitle(job)}
                      onPointerDown={(event) => event.stopPropagation()}
                      onContextMenu={(event) => handleSelectionChipContextMenu(job, event)}
                      onClick={() => handleSelectionChipOpen(job)}
                    >
                      {job.isImportant ? (
                        <span className={styles.annotationChipStar} aria-hidden="true">
                          *
                        </span>
                      ) : null}
                      <span className={styles.annotationChipDot} aria-hidden="true" />
                      <span className={styles.annotationChipText} aria-hidden="true">
                        <span className={styles.annotationChipStatusLabel}>{buildSelectionChipLabel(job)}</span>
                        <span className={styles.annotationChipHoverLabel}>{buildSelectionChipHoverLabel(job)}</span>
                      </span>
                    </button>
                  ))}
                  {chipContextMenu && chipContextMenuJob ? (
                    <div
                      className={styles.chipContextMenu}
                      style={{
                        left: `${chipContextMenu.left}px`,
                        top: `${chipContextMenu.top}px`,
                      }}
                      onPointerDown={(event) => event.stopPropagation()}
                      onContextMenu={(event) => {
                        event.preventDefault();
                        event.stopPropagation();
                      }}
                    >
                      <button
                        type="button"
                        className={styles.chipContextMenuButton}
                        disabled={!chipContextMenuJob.explanation}
                        onClick={() => {
                          void handleToggleSelectionJobImportant(chipContextMenuJob);
                        }}
                      >
                        {chipContextMenuJob.isImportant ? "중요 표시 해제" : "중요 표시"}
                      </button>
                      <button
                        type="button"
                        className={`${styles.chipContextMenuButton} ${styles.chipContextMenuDanger}`}
                        onClick={() => {
                          void handleDeleteSelectionJob(chipContextMenuJob);
                        }}
                      >
                        삭제
                      </button>
                    </div>
                  ) : null}
                  {activeSelectionJob && activeSelectionJob.status !== "ready" && panelPlacement ? (
                    <aside
                      className={`${styles.loadingAnnotationPanel} ${
                        activeSelectionJob.status === "error" ? styles.loadingAnnotationPanelError : ""
                      }`}
                      style={{
                        left: `${panelPlacement.panelStyle.left}px`,
                        top: `${panelPlacement.panelStyle.top}px`,
                        width: `${panelPlacement.panelStyle.width}px`,
                      }}
                      onPointerDown={(event) => event.stopPropagation()}
                    >
                      <span className={styles.loadingPanelEyebrow}>Selected explanation</span>
                      <div className={styles.loadingPanelTitle}>
                        {activeSelectionJob.status === "error" ? "설명을 만들지 못했어" : "문서 맥락을 읽는 중"}
                      </div>
                      <p className={styles.loadingPanelText}>
                        {activeSelectionJob.status === "error"
                          ? activeSelectionJob.errorMessage ?? "선택 영역 설명을 생성할 수 없어."
                          : "선택한 영역을 문서/페이지 맥락과 맞춰 읽고 있어. 다른 곳도 바로 드래그해둘 수 있어."}
                      </p>
                      {activeSelectionJob.status === "pending" ? (
                        <div className={styles.loadingPanelBars} aria-hidden="true">
                          <span />
                          <span />
                          <span />
                        </div>
                      ) : null}
                      <button
                        type="button"
                        className={styles.retrySelectionButton}
                        onPointerDown={(event) => event.stopPropagation()}
                        onClick={handleCancelActiveSelection}
                      >
                        {activeSelectionJob.status === "pending" ? "생성 취소" : "닫고 다시 드래그"}
                      </button>
                    </aside>
                  ) : null}
                  {viewerNotice ? (
                    <div
                      className={`${styles.selectionNotice} ${
                        viewerNotice.tone === "error" ? styles.selectionNoticeError : ""
                      }`}
                      style={{
                        left: `${viewerNotice.left}px`,
                        top: `${viewerNotice.top}px`,
                      }}
                    >
                      {viewerNotice.message}
                    </div>
                  ) : null}
                  {selectedExplanation && panelPlacement && selectedRegionRect ? (
                    <SelectedExplanationPanel
                      explanation={selectedExplanation}
                      currentPage={currentPage}
                      panelStyle={{
                        left: `${panelPlacement.panelStyle.left}px`,
                        top: `${panelPlacement.panelStyle.top}px`,
                        width: `${panelPlacement.panelStyle.width}px`,
                      }}
                      connectorLine={panelPlacement.connectorLine}
                      selectedRect={selectedRegionRect}
                      canvasWidth={panelPlacement.canvasWidth}
                      canvasHeight={panelPlacement.canvasHeight}
                      onNavigateToRelatedPage={handleRelatedPageNavigate}
                      onClose={handleCloseSelectedExplanation}
                    />
                  ) : null}
                </div>
              ) : (
                <div className={styles.stateBlock}>페이지 데이터를 아직 표시할 수 없어.</div>
              )}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
