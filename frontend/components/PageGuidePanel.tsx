import type { PageData, PageGuide, PageWrapUp } from "@/lib/api";
import type { ResponseLanguage } from "@/lib/language";

import styles from "./PageGuidePanel.module.css";

type PageGuidePanelProps = {
  pageGuide: PageGuide | null;
  viewerMode: PageData["viewer_mode"];
  pageRole: string;
  pageSummary: string;
  responseLanguage: ResponseLanguage;
};

type WrapUpPanelProps = {
  wrapUp: PageWrapUp | null;
  viewerMode: PageData["viewer_mode"];
  responseLanguage: ResponseLanguage;
};

type GuideItem = {
  key: string;
  title: string;
  content: string | string[] | null | undefined;
};

function hasText(value: string | null | undefined): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function cleanList(values: string[] | null | undefined): string[] {
  if (!Array.isArray(values)) {
    return [];
  }

  return values.map((value) => value.trim()).filter(Boolean);
}

function hasContent(content: GuideItem["content"]): boolean {
  return Array.isArray(content) ? cleanList(content).length > 0 : hasText(content);
}

function modeLabel(viewerMode: PageData["viewer_mode"]): string {
  if (viewerMode === "render_only") {
    return "Preparing";
  }
  if (viewerMode === "page_context_ready") {
    return "Page context";
  }
  if (viewerMode === "legacy_pass2") {
    return "Legacy";
  }
  return "Ready";
}

function localizedModeLabel(viewerMode: PageData["viewer_mode"], language: ResponseLanguage): string {
  if (language === "en") {
    return modeLabel(viewerMode);
  }
  if (viewerMode === "render_only") {
    return "준비 중";
  }
  if (viewerMode === "page_context_ready") {
    return "페이지 맵";
  }
  if (viewerMode === "legacy_pass2") {
    return "레거시";
  }
  return "사용 가능";
}

const COPY = {
  ko: {
    pageGuideLabel: "페이지 가이드",
    wrapUpLabel: "Wrap-up",
    preparingGuideTitle: "페이지 가이드 준비 중...",
    preparingGuideBody: "전처리가 끝나면 이 페이지를 읽기 전에 볼 방향 안내가 여기에 붙어.",
    preparingWrapTitle: "Wrap-up 준비 중...",
    preparingWrapBody: "읽고 난 뒤 짧게 확인할 정리는 전처리가 끝나면 여기에 붙어.",
    emptyText: "아직 정리된 내용 없음",
    pageRole: "Page Role",
    previousConnection: "Previous Connection",
    thesis: "One-line Thesis",
    logicFlow: "Logic Flow",
    studyFocus: "Study Focus",
    mustRemember: "Must Remember",
    nextConnection: "Next Connection",
  },
  en: {
    pageGuideLabel: "Page Guide",
    wrapUpLabel: "Wrap-up",
    preparingGuideTitle: "Preparing page guide...",
    preparingGuideBody: "Orientation for this page appears here after preprocessing.",
    preparingWrapTitle: "Preparing wrap-up...",
    preparingWrapBody: "A short review strip appears here after preprocessing.",
    emptyText: "No guide content yet",
    pageRole: "Page Role",
    previousConnection: "Previous Connection",
    thesis: "One-line Thesis",
    logicFlow: "Logic Flow",
    studyFocus: "Study Focus",
    mustRemember: "Must Remember",
    nextConnection: "Next Connection",
  },
} satisfies Record<ResponseLanguage, Record<string, string>>;

function renderContent(content: GuideItem["content"], emptyText: string, ordered = false) {
  if (Array.isArray(content)) {
    const values = cleanList(content);
    if (values.length === 0) {
      return <p className={styles.emptyText}>{emptyText}</p>;
    }

    const ListTag = ordered ? "ol" : "ul";
    return (
      <ListTag className={ordered ? styles.orderedList : styles.list}>
        {values.map((value, index) => (
          <li key={`${value}-${index}`}>{value}</li>
        ))}
      </ListTag>
    );
  }

  if (!hasText(content)) {
    return <p className={styles.emptyText}>{emptyText}</p>;
  }

  return <p className={styles.bodyText}>{content}</p>;
}

function GuideRow({
  item,
  emptyText,
  ordered = false,
}: {
  item: GuideItem;
  emptyText: string;
  ordered?: boolean;
}) {
  return (
    <section className={styles.guideRow}>
      <h3>{item.title}</h3>
      {renderContent(item.content, emptyText, ordered)}
    </section>
  );
}

export function PageGuidePanel({
  pageGuide,
  viewerMode,
  pageRole,
  pageSummary,
  responseLanguage,
}: PageGuidePanelProps) {
  const copy = COPY[responseLanguage];
  const statusLabel = localizedModeLabel(viewerMode, responseLanguage);

  if (!pageGuide) {
    return (
      <aside className={`${styles.panel} ${styles.panelPreparing}`} aria-label={copy.pageGuideLabel}>
        <div className={styles.header}>
          <div>
            <span className={styles.eyebrow}>{copy.pageGuideLabel}</span>
            <h2>{copy.preparingGuideTitle}</h2>
          </div>
          <span className={styles.statusPill}>{statusLabel}</span>
        </div>
        <p className={styles.bodyText}>{copy.preparingGuideBody}</p>
      </aside>
    );
  }

  const items: GuideItem[] = [
    { key: "role", title: copy.pageRole, content: pageGuide.page_role || pageRole },
    {
      key: "previous",
      title: copy.previousConnection,
      content: pageGuide.previous_slide_connection,
    },
    { key: "thesis", title: copy.thesis, content: pageGuide.one_line_thesis || pageSummary },
  ];

  return (
    <aside className={styles.panel} aria-label={copy.pageGuideLabel}>
      <div className={styles.header}>
        <div>
          <span className={styles.eyebrow}>{copy.pageGuideLabel}</span>
          <h2>{copy.pageGuideLabel}</h2>
        </div>
        <span className={styles.statusPill}>{statusLabel}</span>
      </div>

      <div className={styles.guideGrid}>
        {items.map((item) => (
          <GuideRow key={item.key} item={item} emptyText={copy.emptyText} />
        ))}
      </div>
    </aside>
  );
}

export function WrapUpPanel({ wrapUp, viewerMode, responseLanguage }: WrapUpPanelProps) {
  const copy = COPY[responseLanguage];
  const statusLabel = localizedModeLabel(viewerMode, responseLanguage);

  if (!wrapUp) {
    return (
      <aside className={`${styles.panel} ${styles.wrapPanel} ${styles.panelPreparing}`} aria-label={copy.wrapUpLabel}>
        <div className={styles.header}>
          <div>
            <span className={styles.eyebrow}>{copy.wrapUpLabel}</span>
            <h2>{copy.preparingWrapTitle}</h2>
          </div>
          <span className={styles.statusPill}>{statusLabel}</span>
        </div>
        <p className={styles.bodyText}>{copy.preparingWrapBody}</p>
      </aside>
    );
  }

  const items: GuideItem[] = [
    { key: "logic", title: copy.logicFlow, content: cleanList(wrapUp.logic_flow) },
    { key: "focus", title: copy.studyFocus, content: wrapUp.study_focus },
    { key: "remember", title: copy.mustRemember, content: cleanList(wrapUp.must_remember) },
    { key: "next", title: copy.nextConnection, content: wrapUp.next_slide_connection },
  ];
  const hasAnyContent = items.some((item) => hasContent(item.content));

  return (
    <aside className={`${styles.panel} ${styles.wrapPanel}`} aria-label={copy.wrapUpLabel}>
      <details open={hasAnyContent} className={styles.wrapDetails}>
        <summary>
          <span>
            <span className={styles.eyebrow}>{copy.wrapUpLabel}</span>
            <strong>{copy.wrapUpLabel}</strong>
          </span>
          <span className={styles.statusPill}>{statusLabel}</span>
        </summary>

        <div className={styles.wrapGrid}>
          {items.map((item) => (
            <GuideRow
              key={item.key}
              item={item}
              emptyText={copy.emptyText}
              ordered={item.key === "logic"}
            />
          ))}
        </div>
      </details>
    </aside>
  );
}
