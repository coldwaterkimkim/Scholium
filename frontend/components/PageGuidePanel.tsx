import type { PageData, PageGuide } from "@/lib/api";

import styles from "./PageGuidePanel.module.css";

type PageGuidePanelProps = {
  pageGuide: PageGuide | null;
  viewerMode: PageData["viewer_mode"];
  pageRole: string;
  pageSummary: string;
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
  ordered = false,
}: {
  title: string;
  content: string | string[] | null | undefined;
  ordered?: boolean;
}) {
  return (
    <section className={styles.primaryCard}>
      <h3>{title}</h3>
      {Array.isArray(content)
        ? renderContent(content, "아직 정리된 읽기 순서 없음", ordered)
        : renderContent(content)}
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
}: PageGuidePanelProps) {
  const statusLabel = modeLabel(viewerMode);

  if (!pageGuide) {
    return (
      <aside className={`${styles.panel} ${styles.panelPreparing}`} aria-label="Page Guide">
        <div className={styles.header}>
          <div>
            <span className={styles.eyebrow}>Page Guide</span>
            <h2>Preparing page guide...</h2>
          </div>
          <span className={styles.statusPill}>{statusLabel}</span>
        </div>
        <p className={styles.bodyText}>PDF는 먼저 읽을 수 있고, 페이지 안내는 전처리가 끝나면 여기에 붙어.</p>
      </aside>
    );
  }

  const role = pageGuide.page_role || pageRole;
  const thesis = pageGuide.one_line_thesis || pageSummary;
  const readingPath = cleanList(pageGuide.reading_path);
  const keyConcepts = pageGuide.key_concepts ?? [];
  const connection = pageGuide.before_next_connection;
  const connectionItems = [
    connection?.previous ? `Previous: ${connection.previous}` : "",
    connection?.next ? `Next: ${connection.next}` : "",
  ].filter(Boolean);
  const detailSections: SectionConfig[] = [
    { key: "logic", title: "Logic Flow", content: cleanList(pageGuide.logic_flow) },
    { key: "omitted", title: "Omitted Context", content: cleanList(pageGuide.omitted_context) },
    { key: "focus", title: "Study Focus", content: cleanList(pageGuide.study_focus) },
    { key: "confusions", title: "Common Confusions", content: cleanList(pageGuide.common_confusions) },
    { key: "example", title: "Example / Application", content: pageGuide.example_or_application },
    { key: "remember", title: "Must Remember", content: cleanList(pageGuide.must_remember) },
    { key: "self-check", title: "Self-check Questions", content: cleanList(pageGuide.self_check_questions) },
    { key: "connection", title: "Before / Next", content: connectionItems },
  ];
  const hasDetails =
    keyConcepts.length > 0 || detailSections.some((section) => hasSectionContent(section.content));

  return (
    <aside className={styles.panel} aria-label="Page Guide">
      <div className={styles.header}>
        <div>
          <span className={styles.eyebrow}>Page Guide</span>
          <h2>{role}</h2>
        </div>
        <span className={styles.statusPill}>{statusLabel}</span>
      </div>

      <div className={styles.primaryGrid}>
        <PrimaryCard title="Page Role" content={role} />
        <PrimaryCard title="One-line Thesis" content={thesis} />
        <PrimaryCard title="Key Question" content={pageGuide.key_question} />
        <PrimaryCard title="Reading Path" content={readingPath} ordered />
      </div>

      {hasDetails ? (
        <details className={styles.moreDetails} open>
          <summary>More guide</summary>
          <div className={styles.detailGrid}>
            {keyConcepts.length > 0 ? (
              <section className={`${styles.detailSection} ${styles.keyConceptSection}`}>
                <h3>Key Concepts</h3>
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
        <p className={styles.legacyNote}>이전 처리 artifact라 페이지 역할과 요약 중심으로만 표시 중이야.</p>
      )}
    </aside>
  );
}
