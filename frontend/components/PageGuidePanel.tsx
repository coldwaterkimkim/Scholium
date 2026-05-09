import type { PageData, PageGuide } from "@/lib/api";
import type { ResponseLanguage } from "@/lib/language";

import styles from "./PageGuidePanel.module.css";

type PageGuidePanelProps = {
  pageGuide: PageGuide | null;
  viewerMode: PageData["viewer_mode"];
  pageRole: string;
  pageSummary: string;
  responseLanguage: ResponseLanguage;
};

type SectionConfig = {
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

function hasSectionContent(content: SectionConfig["content"]): boolean {
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
    label: "페이지 가이드",
    preparingTitle: "페이지 가이드 준비 중...",
    preparingBody: "PDF는 먼저 읽을 수 있고, 페이지 안내는 전처리가 끝나면 여기에 붙어.",
    emptyText: "아직 정리된 내용 없음",
    emptyReadingPath: "아직 정리된 읽기 순서 없음",
    pageRole: "페이지 역할",
    thesis: "한 줄 핵심",
    keyQuestion: "핵심 질문",
    readingPath: "읽기 순서",
    moreGuide: "더 보기",
    keyConcepts: "핵심 개념",
    logic: "논리 흐름",
    omitted: "생략된 맥락",
    focus: "공부 포인트",
    confusions: "헷갈리기 쉬운 점",
    example: "예시 / 적용",
    remember: "꼭 기억할 것",
    selfCheck: "자가 점검 질문",
    connection: "이전 / 다음 연결",
    previous: "이전",
    next: "다음",
    legacyNote: "이전 처리 artifact라 페이지 역할과 요약 중심으로만 표시 중이야.",
  },
  en: {
    label: "Page Guide",
    preparingTitle: "Preparing page guide...",
    preparingBody: "The PDF is readable first; page guidance appears here after preprocessing.",
    emptyText: "No guide content yet",
    emptyReadingPath: "No reading path yet",
    pageRole: "Page Role",
    thesis: "One-line Thesis",
    keyQuestion: "Key Question",
    readingPath: "Reading Path",
    moreGuide: "More guide",
    keyConcepts: "Key Concepts",
    logic: "Logic Flow",
    omitted: "Omitted Context",
    focus: "Study Focus",
    confusions: "Common Confusions",
    example: "Example / Application",
    remember: "Must Remember",
    selfCheck: "Self-check Questions",
    connection: "Before / Next",
    previous: "Previous",
    next: "Next",
    legacyNote: "This legacy artifact only has page role and summary.",
  },
} satisfies Record<ResponseLanguage, Record<string, string>>;

function renderList(values: string[], ordered = false) {
  const ListTag = ordered ? "ol" : "ul";
  return (
    <ListTag className={ordered ? styles.orderedList : styles.list}>
      {values.map((value, index) => (
        <li key={`${value}-${index}`}>{value}</li>
      ))}
    </ListTag>
  );
}

function renderContent(
  content: SectionConfig["content"],
  emptyText = "아직 정리된 내용 없음",
  ordered = false,
) {
  if (Array.isArray(content)) {
    const values = cleanList(content);
    if (values.length === 0) {
      return <p className={styles.emptyText}>{emptyText}</p>;
    }
    return renderList(values, ordered);
  }

  if (!hasText(content)) {
    return <p className={styles.emptyText}>{emptyText}</p>;
  }

  return <p className={styles.bodyText}>{content}</p>;
}

function PrimaryCard({
  title,
  content,
  emptyText,
  ordered = false,
}: {
  title: string;
  content: string | string[] | null | undefined;
  emptyText: string;
  ordered?: boolean;
}) {
  return (
    <section className={styles.primaryCard}>
      <h3>{title}</h3>
      {Array.isArray(content)
        ? renderContent(content, emptyText, ordered)
        : renderContent(content, emptyText)}
    </section>
  );
}

function DetailSection({ title, content }: { title: string; content: SectionConfig["content"] }) {
  if (!hasSectionContent(content)) {
    return null;
  }

  return (
    <section className={styles.detailSection}>
      <h3>{title}</h3>
      {Array.isArray(content) ? renderList(cleanList(content)) : renderContent(content)}
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
      <aside className={`${styles.panel} ${styles.panelPreparing}`} aria-label={copy.label}>
        <div className={styles.header}>
          <div>
            <span className={styles.eyebrow}>{copy.label}</span>
            <h2>{copy.preparingTitle}</h2>
          </div>
          <span className={styles.statusPill}>{statusLabel}</span>
        </div>
        <p className={styles.bodyText}>{copy.preparingBody}</p>
      </aside>
    );
  }

  const role = pageGuide.page_role || pageRole;
  const thesis = pageGuide.one_line_thesis || pageSummary;
  const readingPath = cleanList(pageGuide.reading_path);
  const keyConcepts = pageGuide.key_concepts ?? [];
  const connection = pageGuide.before_next_connection;
  const connectionItems = [
    connection?.previous ? `${copy.previous}: ${connection.previous}` : "",
    connection?.next ? `${copy.next}: ${connection.next}` : "",
  ].filter(Boolean);
  const detailSections: SectionConfig[] = [
    { key: "logic", title: copy.logic, content: cleanList(pageGuide.logic_flow) },
    { key: "omitted", title: copy.omitted, content: cleanList(pageGuide.omitted_context) },
    { key: "focus", title: copy.focus, content: cleanList(pageGuide.study_focus) },
    { key: "confusions", title: copy.confusions, content: cleanList(pageGuide.common_confusions) },
    { key: "example", title: copy.example, content: pageGuide.example_or_application },
    { key: "remember", title: copy.remember, content: cleanList(pageGuide.must_remember) },
    { key: "self-check", title: copy.selfCheck, content: cleanList(pageGuide.self_check_questions) },
    { key: "connection", title: copy.connection, content: connectionItems },
  ];
  const hasDetails =
    keyConcepts.length > 0 || detailSections.some((section) => hasSectionContent(section.content));

  return (
    <aside className={styles.panel} aria-label={copy.label}>
      <div className={styles.header}>
        <div>
          <span className={styles.eyebrow}>{copy.label}</span>
          <h2>{role}</h2>
        </div>
        <span className={styles.statusPill}>{statusLabel}</span>
      </div>

      <div className={styles.primaryGrid}>
        <PrimaryCard title={copy.pageRole} content={role} emptyText={copy.emptyText} />
        <PrimaryCard title={copy.thesis} content={thesis} emptyText={copy.emptyText} />
        <PrimaryCard title={copy.keyQuestion} content={pageGuide.key_question} emptyText={copy.emptyText} />
        <PrimaryCard title={copy.readingPath} content={readingPath} emptyText={copy.emptyReadingPath} ordered />
      </div>

      {hasDetails ? (
        <details className={styles.moreDetails}>
          <summary>{copy.moreGuide}</summary>
          <div className={styles.detailGrid}>
            {keyConcepts.length > 0 ? (
              <section className={`${styles.detailSection} ${styles.keyConceptSection}`}>
                <h3>{copy.keyConcepts}</h3>
                <div className={styles.conceptList}>
                  {keyConcepts.map((concept, index) => (
                    <article className={styles.conceptItem} key={`${concept.concept}-${index}`}>
                      <strong>{concept.concept}</strong>
                      {hasText(concept.brief_description) ? <p>{concept.brief_description}</p> : null}
                      {hasText(concept.role_on_page) ? <span>{concept.role_on_page}</span> : null}
                    </article>
                  ))}
                </div>
              </section>
            ) : null}
            {detailSections.map((section) => (
              <DetailSection key={section.key} title={section.title} content={section.content} />
            ))}
          </div>
        </details>
      ) : (
        <p className={styles.legacyNote}>{copy.legacyNote}</p>
      )}
    </aside>
  );
}
