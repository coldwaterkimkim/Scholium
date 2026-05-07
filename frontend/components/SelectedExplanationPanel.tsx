import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type FormEvent,
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

function scoreDots(score: number) {
  return Array.from({ length: 5 }, (_, index) => index < score);
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
  onNavigateToRelatedPage,
  onClose,
}: SelectedExplanationPanelProps) {
  const conceptTitle = "concept_title" in explanation ? explanation.concept_title : explanation.label;
  const sourceId = "selection_id" in explanation ? explanation.selection_id : explanation.anchor_id;
  const isSelectionExplanation = "selection_id" in explanation && "document_id" in explanation;
  const meaningInContext = explanation.meaning_in_context || explanation.short_explanation;
  const whyItMattersHere = explanation.why_it_matters_here || explanation.long_explanation;
  const relatedConcepts = normalizeRelatedConcepts(explanation, currentPage);
  const sourceCues = explanation.source_cues ?? [];
  const confidencePercent = Number.isFinite(explanation.confidence)
    ? `${Math.round(explanation.confidence * 100)}%`
    : null;
  const panelRef = useRef<HTMLElement | null>(null);
  const panelActionRef = useRef<PanelAction | null>(null);
  const followUpAbortRef = useRef<AbortController | null>(null);
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
    setPanelRect(
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
    setFollowUps([]);
    setFollowUpInput("");
    setIsFollowUpPending(false);
    setPendingFollowUpQuestion(null);
    setFollowUpError(null);
    followUpAbortRef.current?.abort();
    followUpAbortRef.current = null;
  }, [canvasHeight, canvasWidth, panelStyle.left, panelStyle.top, panelStyle.width, sourceId]);

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

  function handlePanelActionMove(event: ReactPointerEvent<HTMLElement>) {
    const action = panelActionRef.current;
    if (!action) {
      return;
    }

    event.preventDefault();
    event.stopPropagation();

    const deltaX = event.clientX - action.startClientX;
    const deltaY = event.clientY - action.startClientY;

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

  function finishPanelAction(event: ReactPointerEvent<HTMLElement>) {
    const action = panelActionRef.current;
    if (!action) {
      return;
    }

    event.preventDefault();
    event.stopPropagation();
    try {
      event.currentTarget.releasePointerCapture(action.pointerId);
    } catch {
      // Browser may already have released capture.
    }
    panelActionRef.current = null;
  }

  function startPanelDrag(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.button !== 0) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    panelActionRef.current = {
      mode: "drag",
      pointerId: event.pointerId,
      startClientX: event.clientX,
      startClientY: event.clientY,
      startRect: getCurrentPanelRect(),
    };
  }

  function startPanelResize(event: ReactPointerEvent<HTMLButtonElement>) {
    if (event.button !== 0) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    panelActionRef.current = {
      mode: "resize",
      pointerId: event.pointerId,
      startClientX: event.clientX,
      startClientY: event.clientY,
      startRect: getCurrentPanelRect(),
    };
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
        controller.signal,
      );
      setFollowUps((previous) => [...previous, answer].slice(-3));
      setFollowUpInput("");
    } catch (error) {
      if (!controller.signal.aborted) {
        setFollowUpError(error instanceof Error ? error.message : "추가 질문에 답할 수 없어.");
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
        className={styles.panel}
        style={computedPanelStyle}
        aria-label={`Selected explanation: ${conceptTitle}`}
        onPointerDown={(event) => event.stopPropagation()}
      >
        <div
          className={styles.windowBar}
          onPointerDown={startPanelDrag}
          onPointerMove={handlePanelActionMove}
          onPointerUp={finishPanelAction}
          onPointerCancel={finishPanelAction}
          title="드래그해서 패널 이동"
        >
          <span className={styles.windowBarHandle} />
          <button
            type="button"
            className={styles.closeButton}
            aria-label="설명 패널 닫기"
            onPointerDown={(event) => event.stopPropagation()}
            onClick={onClose}
          >
            ×
          </button>
        </div>

        <div className={styles.panelContent}>
          <div className={styles.header}>
            <span className={styles.eyebrow}>Selected explanation</span>
            <h2 className={styles.title}>{conceptTitle}</h2>
          </div>

          {explanation.study_importance ? (
            <section className={styles.importanceRow} aria-label="Study importance">
              <div
                className={styles.importanceLabel}
                title="학습 중요도는 문서 핵심 주제와의 관련성, 이후 개념의 prerequisite 여부, 반복 출현 가능성, 시험/복습 가치 기준이야."
              >
                Study Importance
              </div>
              <div className={styles.importanceValue}>
                <span className={styles.importanceLevel}>{explanation.study_importance.level}</span>
                <span className={styles.dots} aria-label={`score ${explanation.study_importance.score} of 5`}>
                  {scoreDots(explanation.study_importance.score).map((isActive, index) => (
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
              <p className={styles.criteriaText}>중심성, prerequisite, 반복 가능성, 복습 가치를 함께 본 값이야.</p>
            </section>
          ) : null}

          <section className={styles.section}>
            <h3 className={styles.sectionTitle}>Meaning in context</h3>
            <p className={styles.bodyText}>{meaningInContext}</p>
          </section>

          <section className={styles.section}>
            <h3 className={styles.sectionTitle}>Why it matters here</h3>
            <p className={styles.bodyText}>{whyItMattersHere}</p>
          </section>

          {relatedConcepts.length > 0 ? (
            <section className={styles.section}>
              <h3 className={styles.sectionTitle}>Related concepts and pages</h3>
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
            <h3 className={styles.sourceTitle}>Source</h3>
            {sourceCues.length > 0 ? (
              <div className={styles.sourceList}>
                {sourceCues.map((cue, index) => (
                  <div key={`${cue.source_type}-${cue.label}-${index}`} className={styles.sourceCue}>
                    <span className={styles.sourceChip}>{cue.source_type.replace("_", " ")}</span>
                    <div>
                      <div className={styles.sourceLabel}>{sourceCueLabel(cue)}</div>
                      {cue.snippet ? <div className={styles.sourceSnippet}>{cue.snippet}</div> : null}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <p className={styles.sourceFallback}>Source cues unavailable for this artifact.</p>
            )}
          </section>

          {confidencePercent ? (
            <div
              className={styles.footerMeta}
              title="Confidence는 정답 보증률이 아니라 선택 영역, page context, source cues가 설명을 얼마나 잘 지지하는지에 대한 grounding 강도야."
            >
              <span>Confidence</span>
              <strong>{confidencePercent}</strong>
              <p className={styles.confidenceCriteria}>선택 bbox와 source cues가 설명을 지지하는 정도.</p>
            </div>
          ) : null}

          <section className={styles.followUpSection}>
            <form className={styles.followUpForm} onSubmit={handleFollowUpSubmit}>
              <input
                className={styles.followUpInput}
                value={followUpInput}
                onChange={(event) => setFollowUpInput(event.target.value)}
                placeholder="더 깊게 물어보기"
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
          aria-label="패널 크기 조절"
          onPointerDown={startPanelResize}
          onPointerMove={handlePanelActionMove}
          onPointerUp={finishPanelAction}
          onPointerCancel={finishPanelAction}
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
            <span>Follow-up</span>
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
                <div className={styles.followUpQuestion}>답변 생성 중...</div>
                <p className={styles.followUpPendingText}>
                  {pendingFollowUpQuestion ?? "질문을 Codex CLI로 보내고 있어."}
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
