import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type FormEvent,
  type MouseEvent as ReactMouseEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react";

import {
  createSelectionFollowUp,
  type LegacyPrecomputedAnchor,
  type RelatedConceptPage,
  type SelectionExplanation,
  type SelectionFollowUp,
  type SourceCue,
} from "@/lib/api";
import type { ResponseLanguage } from "@/lib/language";

import styles from "./SelectedExplanationPanel.module.css";

type ConnectorLine = {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
};

type SelectedRegionRect = {
  left: number;
  top: number;
  width: number;
  height: number;
};

type SelectedExplanationPanelProps = {
  explanation: LegacyPrecomputedAnchor | SelectionExplanation;
  currentPage: number;
  panelStyle: CSSProperties;
  connectorLine: ConnectorLine;
  selectedRect: SelectedRegionRect;
  canvasWidth: number;
  canvasHeight: number;
  responseLanguage: ResponseLanguage;
  onNavigateToRelatedPage: (item: RelatedConceptPage, sourceId: string) => void;
  onClose: () => void;
};

type PanelRect = {
  left: number;
  top: number;
  width: number;
  height: number | null;
};

type ConnectorRect = {
  left: number;
  top: number;
  width: number;
  height: number;
};

type PanelAction =
  | {
      mode: "drag";
      pointerId: number;
      startClientX: number;
      startClientY: number;
      startRect: PanelRect;
    }
  | {
      mode: "resize";
      pointerId: number;
      startClientX: number;
      startClientY: number;
      startRect: PanelRect;
    };

const PANEL_COPY = {
  ko: {
    ariaLabel: "선택 설명",
    eyebrow: "선택 설명",
    studyImportance: "Study Importance",
    studyImportanceTitle:
      "Study Importance는 선택 대상이 이 페이지를 이해하는 데 얼마나 중심적인지 보는 값이야.",
    criteriaText: "애매하면 Medium으로 두고, High는 진짜 중심 개념에만 써.",
    focusType: "focus",
    whatThisIs: "What this is",
    whatItMeansHere: "What it means here",
    omittedContext: "Omitted Context",
    commonConfusion: "Common Confusion",
    exampleOrApplication: "Example / Application",
    related: "Related concepts and pages",
    source: "Source cues",
    sourceFallback: "이 결과에는 근거 단서가 없어.",
    confidence: "신뢰도",
    confidenceTitle:
      "신뢰도는 정답 보증률이 아니라 선택 영역, 페이지 맥락, 근거 단서가 설명을 얼마나 잘 지지하는지에 대한 근거 강도야.",
    confidenceCriteria: "선택 영역과 근거 단서가 설명을 지지하는 정도.",
    followUpPlaceholder: "더 깊게 물어보기",
    followUpError: "추가 질문에 답할 수 없어.",
    followUp: "추가 질문",
    pendingTitle: "답변 생성 중...",
    pendingText: "질문을 Codex CLI로 보내고 있어.",
    resizeLabel: "패널 크기 조절",
    dragTitle: "드래그해서 패널 이동",
    closeLabel: "설명 패널 닫기",
    scoreLabel: "점수",
  },
  en: {
    ariaLabel: "Selected explanation",
    eyebrow: "Selected explanation",
    studyImportance: "Study Importance",
    studyImportanceTitle:
      "Study Importance reflects how central this exact selected target is to understanding the page.",
    criteriaText: "If uncertain, Medium is preferred; High is reserved for truly central selections.",
    focusType: "focus",
    whatThisIs: "What this is",
    whatItMeansHere: "What it means here",
    omittedContext: "Omitted Context",
    commonConfusion: "Common Confusion",
    exampleOrApplication: "Example / Application",
    related: "Related concepts and pages",
    source: "Source cues",
    sourceFallback: "Source cues unavailable for this artifact.",
    confidence: "Confidence",
    confidenceTitle: "Confidence is grounding strength, not a guarantee of correctness.",
    confidenceCriteria: "How strongly the selected bbox and source cues support this explanation.",
    followUpPlaceholder: "Ask a deeper question",
    followUpError: "Could not answer the follow-up.",
    followUp: "Follow-up",
    pendingTitle: "Generating answer...",
    pendingText: "Sending the question to Codex CLI.",
    resizeLabel: "Resize panel",
    dragTitle: "Drag to move panel",
    closeLabel: "Close explanation panel",
    scoreLabel: "score",
  },
} satisfies Record<ResponseLanguage, Record<string, string>>;

function scoreDots(score: number) {
  return Array.from({ length: 5 }, (_, index) => index < score);
}

function localizedImportanceLevel(level: string, responseLanguage: ResponseLanguage): string {
  const normalized = level.trim().toLowerCase();
  if (responseLanguage === "ko") {
    if (normalized === "high") {
      return "높음";
    }
    if (normalized === "medium" || normalized === "moderate") {
      return "보통";
    }
    if (normalized === "low") {
      return "낮음";
    }
  }
  return level;
}

function getImportanceLevel(studyImportance: LegacyPrecomputedAnchor["study_importance"]): "low" | "medium" | "high" {
  const rawLevel = studyImportance?.importance_level ?? studyImportance?.level ?? "medium";
  const normalized = rawLevel.trim().toLowerCase();
  if (normalized === "high" || normalized === "low") {
    return normalized;
  }
  return "medium";
}

function getImportanceScore(studyImportance: LegacyPrecomputedAnchor["study_importance"]): 1 | 2 | 3 | 4 | 5 {
  if (studyImportance?.score) {
    return studyImportance.score;
  }
  const level = getImportanceLevel(studyImportance);
  if (level === "high") {
    return 5;
  }
  if (level === "low") {
    return 2;
  }
  return 3;
}

function formatFocusType(focusType: string | null | undefined): string | null {
  const normalized = focusType?.trim();
  if (!normalized) {
    return null;
  }
  return normalized.replaceAll("_", " ");
}

function localizedSourceType(sourceType: string, responseLanguage: ResponseLanguage): string {
  const normalized = sourceType.trim().toLowerCase();
  if (responseLanguage === "ko") {
    const labels: Record<string, string> = {
      document_context: "문서 맥락",
      document_guide: "문서 가이드",
      page_context: "페이지 맥락",
      page_guide: "페이지 가이드",
      parser_context: "파서 맥락",
      parser_map: "파서 맵",
      selected_region: "선택 영역",
      this_slide: "현재 슬라이드",
      this_page: "현재 페이지",
    };
    return labels[normalized] ?? sourceType.replaceAll("_", " ");
  }
  return sourceType.replaceAll("_", " ");
}

function normalizeRelatedConcepts(
  explanation: LegacyPrecomputedAnchor,
  currentPage: number,
): RelatedConceptPage[] {
  if (explanation.related_concepts_and_pages?.length) {
    return explanation.related_concepts_and_pages;
  }

  return explanation.related_pages
    .filter((pageNumber) => pageNumber !== currentPage)
    .map((pageNumber) => ({
      concept: "Related page",
      page_number: pageNumber,
      relation_reason: "Legacy artifact only includes the page link, not the relation reason.",
    }));
}

function sourceCueLabel(cue: SourceCue): string {
  if (cue.page_number) {
    return `${cue.label} · p. ${cue.page_number}`;
  }
  return cue.label;
}

function clampNumber(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) {
    return min;
  }
  return Math.min(Math.max(value, min), max);
}

function parsePixelValue(value: CSSProperties[keyof CSSProperties], fallback: number): number {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string") {
    const parsed = Number.parseFloat(value);
    if (Number.isFinite(parsed)) {
      return parsed;
    }
  }
  return fallback;
}

function clampPanelRect(rect: PanelRect, canvasWidth: number, canvasHeight: number): PanelRect {
  const width = Math.round(Math.min(Math.max(rect.width, 320), Math.max(320, canvasWidth - 24)));
  const height =
    rect.height === null
      ? null
      : Math.round(Math.min(Math.max(rect.height, 260), Math.max(260, canvasHeight - 24)));
  const effectiveHeight = height ?? 360;
  const left = Math.round(Math.min(Math.max(rect.left, 12), Math.max(12, canvasWidth - width - 12)));
  const top = Math.round(Math.min(Math.max(rect.top, 12), Math.max(12, canvasHeight - effectiveHeight - 12)));
  return { left, top, width, height };
}

function getRectRight(rect: ConnectorRect): number {
  return rect.left + rect.width;
}

function getRectBottom(rect: ConnectorRect): number {
  return rect.top + rect.height;
}

function getRectCenter(rect: ConnectorRect): { x: number; y: number } {
  return {
    x: rect.left + rect.width / 2,
    y: rect.top + rect.height / 2,
  };
}

function getConnectorPointOnRectEdge(
  rect: ConnectorRect,
  target: { x: number; y: number },
): { x: number; y: number } {
  const right = getRectRight(rect);
  const bottom = getRectBottom(rect);
  const center = getRectCenter(rect);

  if (target.x > right) {
    return { x: right, y: clampNumber(target.y, rect.top, bottom) };
  }
  if (target.x < rect.left) {
    return { x: rect.left, y: clampNumber(target.y, rect.top, bottom) };
  }
  if (target.y > bottom) {
    return { x: clampNumber(target.x, rect.left, right), y: bottom };
  }
  if (target.y < rect.top) {
    return { x: clampNumber(target.x, rect.left, right), y: rect.top };
  }

  if (Math.abs(target.x - center.x) >= Math.abs(target.y - center.y)) {
    return {
      x: target.x >= center.x ? right : rect.left,
      y: clampNumber(target.y, rect.top, bottom),
    };
  }

  return {
    x: clampNumber(target.x, rect.left, right),
    y: target.y >= center.y ? bottom : rect.top,
  };
}

function buildConnectorLine(selectedRect: ConnectorRect, panelRect: ConnectorRect): ConnectorLine {
  const selectedCenter = getRectCenter(selectedRect);
  const panelCenter = getRectCenter(panelRect);
  const selectedPoint = getConnectorPointOnRectEdge(selectedRect, panelCenter);
  const panelPoint = getConnectorPointOnRectEdge(panelRect, selectedCenter);

  return {
    x1: selectedPoint.x,
    y1: selectedPoint.y,
    x2: panelPoint.x,
    y2: panelPoint.y,
  };
}

function renderInlineMarkdown(text: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(text.slice(lastIndex, match.index));
    }

    const token = match[0];
    if (token.startsWith("**")) {
      nodes.push(<strong key={`bold-${match.index}`}>{token.slice(2, -2)}</strong>);
    } else {
      nodes.push(<code key={`code-${match.index}`}>{token.slice(1, -1)}</code>);
    }
    lastIndex = match.index + token.length;
  }

  if (lastIndex < text.length) {
    nodes.push(text.slice(lastIndex));
  }

  return nodes;
}

function renderMarkdownBlocks(markdown: string): ReactNode {
  const lines = markdown.replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index].trim();

    if (!line) {
      index += 1;
      continue;
    }

    const headingMatch = line.match(/^(#{1,3})\s+(.+)$/);
    if (headingMatch) {
      const level = headingMatch[1].length;
      const HeadingTag = level === 1 ? "h4" : "h5";
      blocks.push(
        <HeadingTag key={`heading-${index}`} className={styles.markdownHeading}>
          {renderInlineMarkdown(headingMatch[2])}
        </HeadingTag>,
      );
      index += 1;
      continue;
    }

    if (/^[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (index < lines.length && /^[-*]\s+/.test(lines[index].trim())) {
        items.push(lines[index].trim().replace(/^[-*]\s+/, ""));
        index += 1;
      }
      blocks.push(
        <ul key={`ul-${index}`} className={styles.markdownList}>
          {items.map((item, itemIndex) => (
            <li key={`${item}-${itemIndex}`}>{renderInlineMarkdown(item)}</li>
          ))}
        </ul>,
      );
      continue;
    }

    if (/^\d+\.\s+/.test(line)) {
      const items: string[] = [];
      while (index < lines.length && /^\d+\.\s+/.test(lines[index].trim())) {
        items.push(lines[index].trim().replace(/^\d+\.\s+/, ""));
        index += 1;
      }
      blocks.push(
        <ol key={`ol-${index}`} className={styles.markdownList}>
          {items.map((item, itemIndex) => (
            <li key={`${item}-${itemIndex}`}>{renderInlineMarkdown(item)}</li>
          ))}
        </ol>,
      );
      continue;
    }

    const paragraphLines = [line];
    index += 1;
    while (
      index < lines.length &&
      lines[index].trim() &&
      !/^(#{1,3})\s+/.test(lines[index].trim()) &&
      !/^[-*]\s+/.test(lines[index].trim()) &&
      !/^\d+\.\s+/.test(lines[index].trim())
    ) {
      paragraphLines.push(lines[index].trim());
      index += 1;
    }

    blocks.push(
      <p key={`p-${index}`} className={styles.markdownParagraph}>
        {renderInlineMarkdown(paragraphLines.join(" "))}
      </p>,
    );
  }

  return blocks;
}

export function SelectedExplanationPanel({
  explanation,
  currentPage,
  panelStyle,
  connectorLine,
  selectedRect,
  canvasWidth,
  canvasHeight,
  responseLanguage,
  onNavigateToRelatedPage,
  onClose,
}: SelectedExplanationPanelProps) {
  const copy = PANEL_COPY[responseLanguage];
  const conceptTitle = "concept_title" in explanation ? explanation.concept_title : explanation.label;
  const sourceId = "selection_id" in explanation ? explanation.selection_id : explanation.anchor_id;
  const isSelectionExplanation = "selection_id" in explanation && "document_id" in explanation;
  const importanceLevel = getImportanceLevel(explanation.study_importance);
  const importanceScore = getImportanceScore(explanation.study_importance);
  const importanceFocusType = formatFocusType(explanation.study_importance?.focus_type);
  const whatThisIs = explanation.what_this_is || explanation.short_explanation;
  const whatItMeansHere =
    explanation.what_it_means_here || explanation.meaning_in_context || explanation.long_explanation;
  const omittedContext = explanation.omitted_context?.trim();
  const commonConfusion = explanation.common_confusion?.trim();
  const exampleOrApplication = explanation.example_or_application?.trim();
  const relatedConcepts = normalizeRelatedConcepts(explanation, currentPage);
  const sourceCues = explanation.source_cues ?? [];
  const confidencePercent = Number.isFinite(explanation.confidence)
    ? `${Math.round(explanation.confidence * 100)}%`
    : null;
  const panelRef = useRef<HTMLElement | null>(null);
  const panelActionRef = useRef<PanelAction | null>(null);
  const followUpAbortRef = useRef<AbortController | null>(null);
  const [panelActionMode, setPanelActionMode] = useState<PanelAction["mode"] | null>(null);
  const [panelRect, setPanelRect] = useState<PanelRect>(() =>
    clampPanelRect(
      {
        left: parsePixelValue(panelStyle.left, 12),
        top: parsePixelValue(panelStyle.top, 12),
        width: parsePixelValue(panelStyle.width, 420),
        height: null,
      },
      canvasWidth,
      canvasHeight,
    ),
  );
  const [followUps, setFollowUps] = useState<SelectionFollowUp[]>([]);
  const [followUpInput, setFollowUpInput] = useState("");
  const [isFollowUpPending, setIsFollowUpPending] = useState(false);
  const [pendingFollowUpQuestion, setPendingFollowUpQuestion] = useState<string | null>(null);
  const [followUpError, setFollowUpError] = useState<string | null>(null);
  const computedPanelStyle: CSSProperties = {
    ...panelStyle,
    left: `${panelRect.left}px`,
    top: `${panelRect.top}px`,
    width: `${panelRect.width}px`,
    height: panelRect.height === null ? panelStyle.height : `${panelRect.height}px`,
  };
  const panelConnectorHeight = panelRect.height ?? panelRef.current?.offsetHeight ?? 620;
  const dynamicConnectorLine = buildConnectorLine(
    {
      left: selectedRect.left,
      top: selectedRect.top,
      width: selectedRect.width,
      height: selectedRect.height,
    },
    {
      left: panelRect.left,
      top: panelRect.top,
      width: panelRect.width,
      height: panelConnectorHeight,
    },
  );
  const connectorMidX = (dynamicConnectorLine.x1 + dynamicConnectorLine.x2) / 2;
  const connectorMidY = (dynamicConnectorLine.y1 + dynamicConnectorLine.y2) / 2;
  const connectorIsMostlyHorizontal =
    Math.abs(dynamicConnectorLine.x2 - dynamicConnectorLine.x1) >=
    Math.abs(dynamicConnectorLine.y2 - dynamicConnectorLine.y1);
  const followUpPanelWidth = Math.round(clampNumber(canvasWidth * 0.26, 300, 360));
  const followUpGap = 10;
  const canPlaceFollowUpRight = panelRect.left + panelRect.width + followUpGap + followUpPanelWidth <= canvasWidth - 12;
  const followUpLeft = canPlaceFollowUpRight
    ? panelRect.left + panelRect.width + followUpGap
    : clampNumber(panelRect.left - followUpPanelWidth - followUpGap, 12, Math.max(12, canvasWidth - followUpPanelWidth - 12));
  const followUpTop = clampNumber(panelRect.top + 32, 12, Math.max(12, canvasHeight - 260));
  const followUpPanelStyle: CSSProperties = {
    left: `${followUpLeft}px`,
    top: `${followUpTop}px`,
    width: `${followUpPanelWidth}px`,
    maxHeight: `${Math.max(240, canvasHeight - followUpTop - 12)}px`,
  };
  const shouldShowFollowUpPanel =
    isSelectionExplanation && (followUps.length > 0 || isFollowUpPending || Boolean(followUpError));

  useEffect(() => {
    setPanelRect((current) => clampPanelRect(current, canvasWidth, canvasHeight));
  }, [canvasHeight, canvasWidth]);

  useEffect(() => {
    const initialRect = clampPanelRect(
      {
        left: parsePixelValue(panelStyle.left, 12),
        top: parsePixelValue(panelStyle.top, 12),
        width: parsePixelValue(panelStyle.width, 420),
        height: null,
      },
      canvasWidth,
      canvasHeight,
    );
    setPanelRect(initialRect);
    setFollowUps([]);
    setFollowUpInput("");
    setIsFollowUpPending(false);
    setPendingFollowUpQuestion(null);
    setFollowUpError(null);
    followUpAbortRef.current?.abort();
    followUpAbortRef.current = null;
  }, [sourceId]);

  useEffect(() => {
    return () => {
      followUpAbortRef.current?.abort();
    };
  }, []);

  function getCurrentPanelRect(): PanelRect {
    const bounds = panelRef.current?.getBoundingClientRect();
    return {
      left: panelRect.left,
      top: panelRect.top,
      width: bounds?.width ?? panelRect.width,
      height: bounds?.height ?? panelRect.height,
    };
  }

  function applyPanelActionMove(clientX: number, clientY: number) {
    const action = panelActionRef.current;
    if (!action) {
      return;
    }

    const deltaX = clientX - action.startClientX;
    const deltaY = clientY - action.startClientY;

    if (action.mode === "drag") {
      setPanelRect(
        clampPanelRect(
          {
            ...action.startRect,
            left: action.startRect.left + deltaX,
            top: action.startRect.top + deltaY,
          },
          canvasWidth,
          canvasHeight,
        ),
      );
      return;
    }

    setPanelRect(
      clampPanelRect(
        {
          ...action.startRect,
          width: action.startRect.width + deltaX,
          height: (action.startRect.height ?? 360) + deltaY,
        },
        canvasWidth,
        canvasHeight,
      ),
    );
  }

  function handlePanelActionMove(event: ReactPointerEvent<HTMLElement>) {
    if (!panelActionRef.current) {
      return;
    }

    event.preventDefault();
    event.stopPropagation();
    applyPanelActionMove(event.clientX, event.clientY);
  }

  function handleNativePanelActionMove(event: MouseEvent) {
    if (!panelActionRef.current) {
      return;
    }

    event.preventDefault();
    applyPanelActionMove(event.clientX, event.clientY);
  }

  function finishNativePanelAction(event?: MouseEvent) {
    if (!panelActionRef.current) {
      return;
    }

    event?.preventDefault();
    panelActionRef.current = null;
    setPanelActionMode(null);
    window.removeEventListener("mousemove", handleNativePanelActionMove);
    window.removeEventListener("mouseup", finishNativePanelAction);
  }

  function finishPanelAction(event: ReactPointerEvent<HTMLElement>) {
    const action = panelActionRef.current;
    if (!action) {
      return;
    }

    event.preventDefault();
    event.stopPropagation();
    try {
      panelRef.current?.releasePointerCapture(action.pointerId);
    } catch {
      // Browser may already have released capture.
    }
    panelActionRef.current = null;
    setPanelActionMode(null);
    window.removeEventListener("mousemove", handleNativePanelActionMove);
    window.removeEventListener("mouseup", finishNativePanelAction);
  }

  function startPanelAction(
    mode: PanelAction["mode"],
    startClientX: number,
    startClientY: number,
    pointerId: number,
  ) {
    panelActionRef.current = {
      mode,
      pointerId,
      startClientX,
      startClientY,
      startRect: getCurrentPanelRect(),
    };
    setPanelActionMode(mode);
  }

  function startPanelDrag(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.button !== 0) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    panelRef.current?.setPointerCapture(event.pointerId);
    startPanelAction("drag", event.clientX, event.clientY, event.pointerId);
  }

  function startPanelResize(event: ReactPointerEvent<HTMLButtonElement>) {
    if (event.button !== 0) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    panelRef.current?.setPointerCapture(event.pointerId);
    startPanelAction("resize", event.clientX, event.clientY, event.pointerId);
  }

  function startPanelDragMouse(event: ReactMouseEvent<HTMLDivElement>) {
    if (event.button !== 0 || panelActionRef.current) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    startPanelAction("drag", event.clientX, event.clientY, -1);
    window.addEventListener("mousemove", handleNativePanelActionMove);
    window.addEventListener("mouseup", finishNativePanelAction);
  }

  function startPanelResizeMouse(event: ReactMouseEvent<HTMLButtonElement>) {
    if (event.button !== 0 || panelActionRef.current) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    startPanelAction("resize", event.clientX, event.clientY, -1);
    window.addEventListener("mousemove", handleNativePanelActionMove);
    window.addEventListener("mouseup", finishNativePanelAction);
  }

  async function handleFollowUpSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const question = followUpInput.trim();
    if (!question || !isSelectionExplanation || isFollowUpPending) {
      return;
    }

    followUpAbortRef.current?.abort();
    const controller = new AbortController();
    followUpAbortRef.current = controller;
    setIsFollowUpPending(true);
    setPendingFollowUpQuestion(question);
    setFollowUpError(null);

    try {
      const answer = await createSelectionFollowUp(
        explanation.document_id,
        explanation.page_number,
        explanation.selection_id,
        question,
        responseLanguage,
        controller.signal,
      );
      setFollowUps((previous) => [...previous, answer].slice(-3));
      setFollowUpInput("");
    } catch (error) {
      if (!controller.signal.aborted) {
        setFollowUpError(error instanceof Error ? error.message : copy.followUpError);
      }
    } finally {
      if (!controller.signal.aborted) {
        setIsFollowUpPending(false);
        setPendingFollowUpQuestion(null);
        followUpAbortRef.current = null;
      }
    }
  }

  return (
    <>
      <svg
        className={styles.connectorLayer}
        width={canvasWidth}
        height={canvasHeight}
        viewBox={`0 0 ${canvasWidth} ${canvasHeight}`}
        aria-hidden="true"
      >
        <path
          className={styles.connectorPath}
          d={
            connectorIsMostlyHorizontal
              ? `M ${dynamicConnectorLine.x1} ${dynamicConnectorLine.y1} C ${connectorMidX} ${dynamicConnectorLine.y1}, ${connectorMidX} ${dynamicConnectorLine.y2}, ${dynamicConnectorLine.x2} ${dynamicConnectorLine.y2}`
              : `M ${dynamicConnectorLine.x1} ${dynamicConnectorLine.y1} C ${dynamicConnectorLine.x1} ${connectorMidY}, ${dynamicConnectorLine.x2} ${connectorMidY}, ${dynamicConnectorLine.x2} ${dynamicConnectorLine.y2}`
          }
        />
        <circle className={styles.connectorDot} cx={dynamicConnectorLine.x1} cy={dynamicConnectorLine.y1} r="4" />
        <circle className={styles.connectorDot} cx={dynamicConnectorLine.x2} cy={dynamicConnectorLine.y2} r="4" />
      </svg>

      <aside
        ref={panelRef}
        className={`${styles.panel} ${panelActionMode ? styles.panelInteracting : ""}`}
        style={computedPanelStyle}
        aria-label={`${copy.ariaLabel}: ${conceptTitle}`}
        onPointerDown={(event) => event.stopPropagation()}
        onPointerMove={handlePanelActionMove}
        onPointerUp={finishPanelAction}
        onPointerCancel={finishPanelAction}
      >
        <div
          className={styles.windowBar}
          onPointerDown={startPanelDrag}
          onMouseDown={startPanelDragMouse}
          title={copy.dragTitle}
        >
          <span className={styles.windowBarHandle} />
          <button
            type="button"
            className={styles.closeButton}
            aria-label={copy.closeLabel}
            onPointerDown={(event) => event.stopPropagation()}
            onClick={onClose}
          >
            ×
          </button>
        </div>

        <div className={styles.panelContent}>
          <div className={styles.header}>
            <span className={styles.eyebrow}>{copy.eyebrow}</span>
            <h2 className={styles.title}>{conceptTitle}</h2>
          </div>

          {explanation.study_importance ? (
            <section className={styles.importanceRow} aria-label={copy.studyImportance}>
              <div
                className={styles.importanceLabel}
                title={copy.studyImportanceTitle}
              >
                {copy.studyImportance}
              </div>
              <div className={styles.importanceValue}>
                <span className={styles.importanceLevel}>
                  {localizedImportanceLevel(importanceLevel, responseLanguage)}
                </span>
                {importanceFocusType ? (
                  <span className={styles.importanceFocus}>
                    {copy.focusType}: {importanceFocusType}
                  </span>
                ) : null}
                <span
                  className={styles.dots}
                  aria-label={`${copy.scoreLabel} ${importanceScore} / 5`}
                >
                  {scoreDots(importanceScore).map((isActive, index) => (
                    <span
                      key={index}
                      className={`${styles.dot} ${isActive ? styles.dotActive : ""}`}
                    />
                  ))}
                </span>
              </div>
              {explanation.study_importance.reason ? (
                <p className={styles.importanceReason}>{explanation.study_importance.reason}</p>
              ) : null}
              <p className={styles.criteriaText}>{copy.criteriaText}</p>
            </section>
          ) : null}

          <section className={styles.section}>
            <h3 className={styles.sectionTitle}>{copy.whatThisIs}</h3>
            <p className={styles.bodyText}>{whatThisIs}</p>
          </section>

          <section className={styles.section}>
            <h3 className={styles.sectionTitle}>{copy.whatItMeansHere}</h3>
            <p className={styles.bodyText}>{whatItMeansHere}</p>
          </section>

          {omittedContext ? (
            <section className={styles.section}>
              <h3 className={styles.sectionTitle}>{copy.omittedContext}</h3>
              <p className={styles.bodyText}>{omittedContext}</p>
            </section>
          ) : null}

          {commonConfusion ? (
            <section className={styles.section}>
              <h3 className={styles.sectionTitle}>{copy.commonConfusion}</h3>
              <p className={styles.bodyText}>{commonConfusion}</p>
            </section>
          ) : null}

          {exampleOrApplication ? (
            <section className={styles.section}>
              <h3 className={styles.sectionTitle}>{copy.exampleOrApplication}</h3>
              <p className={styles.bodyText}>{exampleOrApplication}</p>
            </section>
          ) : null}

          {relatedConcepts.length > 0 ? (
            <section className={styles.section}>
              <h3 className={styles.sectionTitle}>{copy.related}</h3>
              <div className={styles.relatedList}>
                {relatedConcepts.map((item, index) => {
                  const canNavigate =
                    typeof item.page_number === "number" && item.page_number > 0 && item.page_number !== currentPage;
                  const content = (
                    <>
                      <div className={styles.relatedMain}>
                        <span className={styles.relatedConcept}>{item.concept}</span>
                        {item.page_number ? <span className={styles.pageChip}>p. {item.page_number}</span> : null}
                      </div>
                      <p className={styles.relatedReason}>{item.relation_reason}</p>
                      {canNavigate ? <span className={styles.relatedChevron}>›</span> : null}
                    </>
                  );

                  if (canNavigate) {
                    return (
                      <button
                        key={`${item.concept}-${item.page_number ?? "none"}-${index}`}
                        type="button"
                        className={`${styles.relatedRow} ${styles.relatedButton}`}
                        onClick={() => onNavigateToRelatedPage(item, sourceId)}
                      >
                        {content}
                      </button>
                    );
                  }

                  return (
                    <div key={`${item.concept}-${item.page_number ?? "none"}-${index}`} className={styles.relatedRow}>
                      {content}
                    </div>
                  );
                })}
              </div>
            </section>
          ) : null}

          <section className={`${styles.section} ${styles.sourceSection}`}>
            <h3 className={styles.sourceTitle}>{copy.source}</h3>
            {sourceCues.length > 0 ? (
              <div className={styles.sourceList}>
                {sourceCues.map((cue, index) => (
                  <div key={`${cue.source_type}-${cue.label}-${index}`} className={styles.sourceCue}>
                    <span className={styles.sourceChip}>
                      {localizedSourceType(cue.source_type, responseLanguage)}
                    </span>
                    <div>
                      <div className={styles.sourceLabel}>{sourceCueLabel(cue)}</div>
                      {cue.snippet ? <div className={styles.sourceSnippet}>{cue.snippet}</div> : null}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <p className={styles.sourceFallback}>{copy.sourceFallback}</p>
            )}
          </section>

          {confidencePercent ? (
            <div
              className={styles.footerMeta}
              title={copy.confidenceTitle}
            >
              <span>{copy.confidence}</span>
              <strong>{confidencePercent}</strong>
              <p className={styles.confidenceCriteria}>{copy.confidenceCriteria}</p>
            </div>
          ) : null}

          <section className={styles.followUpSection}>
            <form className={styles.followUpForm} onSubmit={handleFollowUpSubmit}>
              <input
                className={styles.followUpInput}
                value={followUpInput}
                onChange={(event) => setFollowUpInput(event.target.value)}
                placeholder={copy.followUpPlaceholder}
                disabled={!isSelectionExplanation || isFollowUpPending}
              />
              <button
                type="submit"
                className={styles.followUpButton}
                disabled={!followUpInput.trim() || !isSelectionExplanation || isFollowUpPending}
              >
                {isFollowUpPending ? "..." : "↵"}
              </button>
            </form>
            {followUpError ? <div className={styles.followUpError}>{followUpError}</div> : null}
          </section>
        </div>

        <button
          type="button"
          className={styles.resizeHandle}
          aria-label={copy.resizeLabel}
          onPointerDown={startPanelResize}
          onMouseDown={startPanelResizeMouse}
        >
          <svg className={styles.resizeIcon} viewBox="0 0 20 20" aria-hidden="true">
            <path d="M7 17L17 7" />
            <path d="M12 17L17 12" />
            <path d="M16 17L17 16" />
          </svg>
        </button>
      </aside>

      {shouldShowFollowUpPanel ? (
        <aside
          className={styles.followUpPanel}
          style={followUpPanelStyle}
          aria-label="Follow-up answer"
          onPointerDown={(event) => event.stopPropagation()}
        >
          <div className={styles.followUpPanelHeader}>
            <span>{copy.followUp}</span>
            <span className={styles.followUpPanelCount}>{followUps.length + (isFollowUpPending ? 1 : 0)}</span>
          </div>
          <div className={styles.followUpPanelBody}>
            {followUps.map((followUp, index) => (
              <article key={`${followUp.question}-${index}`} className={styles.followUpAnswer}>
                <div className={styles.markdownBody}>{renderMarkdownBlocks(followUp.answer)}</div>
              </article>
            ))}
            {isFollowUpPending ? (
              <article className={`${styles.followUpAnswer} ${styles.followUpPending}`}>
                <div className={styles.followUpQuestion}>{copy.pendingTitle}</div>
                <p className={styles.followUpPendingText}>
                  {pendingFollowUpQuestion ?? copy.pendingText}
                </p>
                <div className={styles.followUpSkeleton} aria-hidden="true">
                  <span />
                  <span />
                  <span />
                </div>
              </article>
            ) : null}
            {followUpError ? <div className={styles.followUpPanelError}>{followUpError}</div> : null}
          </div>
        </aside>
      ) : null}
    </>
  );
}
