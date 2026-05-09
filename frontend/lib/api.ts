export type ElementType =
  | "text"
  | "formula"
  | "chart"
  | "table"
  | "diagram"
  | "image"
  | "flow"
  | "other";

export type AnchorType = ElementType;

export type DocumentMeta = {
  document_id: string;
  filename: string;
  status: string;
  total_pages: number | null;
};

export type DocumentListItem = DocumentMeta & {
  created_at: string;
  updated_at: string;
  error_message: string | null;
};

export type DocumentListResponse = {
  documents: DocumentListItem[];
};

export type DocumentUploadResponse = {
  document_id: string;
  status: string;
};

export type ProcessingStage = "render" | "pass1" | "synthesis" | "pass2";

export type ProcessingFailureSummary = {
  page_number: number;
  stage: ProcessingStage;
  error_message: string;
};

export type DocumentProcessing = {
  document_id: string;
  status: string;
  stage: ProcessingStage | null;
  current_stage: ProcessingStage | null;
  total_pages: number | null;
  rendered_pages: number;
  pass1_completed_pages: number;
  pass1_failed_pages: number;
  pass1_processed_pages: number;
  synthesis_ready: boolean;
  semantic_guide_ready: boolean;
  pass2_completed_pages: number;
  pass2_failed_pages: number;
  render_ready_for_viewer: boolean;
  page_context_ready_pages: number;
  parser_map_ready_pages: number;
  document_context_ready: boolean;
  viewer_ready: boolean;
  ready_for_viewer: boolean;
  current_page_number: number | null;
  error_message: string | null;
  has_errors: boolean;
  failed_page_count: number;
  completed_page_count: number;
  completion_ratio: number;
  recent_failures: ProcessingFailureSummary[];
};

export type DocumentSection = {
  section_id: string;
  title: string;
  pages: number[];
};

export type KeyConcept = {
  term: string;
  description: string;
  pages: number[];
};

export type PrerequisiteLink = {
  from_page: number;
  to_page: number;
  reason: string;
};

export type StudyImportance = {
  level: "low" | "medium" | "high";
  score: 1 | 2 | 3 | 4 | 5;
  reason?: string | null;
};

export type RelatedConceptPage = {
  concept: string;
  page_number?: number | null;
  relation_reason: string;
};

export type SourceCue = {
  source_type:
    | "this_slide"
    | "caption"
    | "related_page"
    | "transcript"
    | "document_context"
    | "other";
  label: string;
  page_number?: number | null;
  snippet?: string | null;
};

export type DocumentSummary = {
  document_id: string;
  overall_topic: string;
  overall_summary: string;
  sections: DocumentSection[];
  key_concepts: KeyConcept[];
  difficult_pages: number[];
  prerequisite_links: PrerequisiteLink[];
};

export type LegacyPrecomputedAnchor = {
  anchor_id: string;
  label: string;
  anchor_type: ElementType;
  bbox: [number, number, number, number];
  question: string;
  short_explanation: string;
  long_explanation: string;
  prerequisite: string;
  related_pages: number[];
  confidence: number;
  study_importance?: StudyImportance | null;
  meaning_in_context?: string | null;
  why_it_matters_here?: string | null;
  related_concepts_and_pages?: RelatedConceptPage[] | null;
  source_cues?: SourceCue[] | null;
};

export type PageElement = {
  element_id: string;
  element_type: ElementType;
  anchor_id: string;
  label: string;
  anchor_type: ElementType;
  bbox: [number, number, number, number];
  question: string;
  short_explanation: string;
  confidence: number;
};

export type PageGuideKeyConcept = {
  concept: string;
  brief_description?: string | null;
  role_on_page?: string | null;
};

export type PageGuideConnection = {
  previous?: string | null;
  next?: string | null;
};

export type PageGuide = {
  page_role?: string | null;
  one_line_thesis?: string | null;
  key_question?: string | null;
  reading_path?: string[];
  logic_flow?: string[];
  key_concepts?: PageGuideKeyConcept[];
  omitted_context?: string[];
  study_focus?: string[];
  common_confusions?: string[];
  example_or_application?: string | null;
  must_remember?: string[];
  self_check_questions?: string[];
  before_next_connection?: PageGuideConnection | null;
};

// Compatibility export for old debug components and external callers.
export type FinalAnchor = LegacyPrecomputedAnchor;

export type SelectionExplanation = LegacyPrecomputedAnchor & {
  document_id: string;
  page_number: number;
  selection_id: string;
  concept_title: string;
  selected_bbox: [number, number, number, number];
  explanation_mode: "selection";
  study_importance: StudyImportance;
  meaning_in_context: string;
  why_it_matters_here: string;
  related_concepts_and_pages: RelatedConceptPage[];
  source_cues: SourceCue[];
};

export type SelectionExplanationHistoryItem = {
  explanation: SelectionExplanation;
  is_important: boolean;
};

export type SelectionExplanationHistory = {
  items: SelectionExplanationHistoryItem[];
};

export type SelectionExplanationState = {
  selection_id: string;
  is_important: boolean;
};

export type SelectionFollowUp = {
  document_id: string;
  page_number: number;
  selection_id: string;
  question: string;
  answer: string;
  source_cues: SourceCue[];
  confidence: number | null;
};

export type InteractionEventType =
  | "page_view"
  | "anchor_click"
  | "related_page_jump"
  | "selection_start"
  | "selection_explanation_request"
  | "selection_explanation_success"
  | "selection_explanation_failure";

export type InteractionLogPayload = {
  document_id: string;
  page_number: number;
  anchor_id: string | null;
  event_type: InteractionEventType;
};

export type PageData = {
  document_id: string;
  page_number: number;
  image_url: string;
  page_role: string;
  page_summary: string;
  final_anchors: LegacyPrecomputedAnchor[];
  page_elements: PageElement[];
  page_guide: PageGuide | null;
  document_guide_summary?: {
    overall_topic?: string | null;
    overall_summary?: string | null;
    difficult_pages?: number[];
    key_concepts?: { concept?: string; term?: string; description?: string; pages?: number[] }[];
  } | null;
  page_risk_note: string;
  viewer_mode: "render_only" | "page_context_ready" | "on_demand" | "legacy_pass2";
};

export class ApiRequestError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiRequestError";
    this.status = status;
  }
}

const API_PROXY_BASE = "/backend-api";

function buildApiUrl(path: string): string {
  return `${API_PROXY_BASE}${path}`;
}

function normalizeErrorMessage(status: number, fallbackMessage: string): string {
  if (status === 404) {
    return fallbackMessage;
  }

  if (status >= 500) {
    return "서버 오류가 발생했어.";
  }

  return "데이터를 불러오지 못했어.";
}

async function readErrorMessage(response: Response, fallbackMessage: string): Promise<string> {
  try {
    const payload = (await response.json()) as { detail?: string };
    if (typeof payload.detail === "string" && payload.detail.trim()) {
      return payload.detail;
    }
  } catch {
    return normalizeErrorMessage(response.status, fallbackMessage);
  }

  return normalizeErrorMessage(response.status, fallbackMessage);
}

async function fetchJson<T>(
  path: string,
  fallbackMessage: string,
  signal?: AbortSignal,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(buildApiUrl(path), {
    ...init,
    cache: "no-store",
    headers: {
      ...(init?.headers ?? {}),
    },
    signal: init?.signal ?? signal,
  });

  if (!response.ok) {
    throw new ApiRequestError(normalizeErrorMessage(response.status, fallbackMessage), response.status);
  }

  try {
    return (await response.json()) as T;
  } catch {
    throw new ApiRequestError("서버 오류가 발생했어.", response.status);
  }
}

function wait(milliseconds: number, signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) {
    return Promise.reject(new DOMException("The operation was aborted.", "AbortError"));
  }

  return new Promise((resolve, reject) => {
    const timeoutId = window.setTimeout(resolve, milliseconds);

    function handleAbort() {
      window.clearTimeout(timeoutId);
      reject(new DOMException("The operation was aborted.", "AbortError"));
    }

    signal?.addEventListener("abort", handleAbort, { once: true });
  });
}

function isTransientApiError(error: unknown): boolean {
  return error instanceof ApiRequestError && error.status >= 500;
}

export function getDocument(documentId: string, signal?: AbortSignal): Promise<DocumentMeta> {
  return fetchJson<DocumentMeta>(
    `/api/documents/${encodeURIComponent(documentId)}`,
    "문서를 찾을 수 없어.",
    signal,
  );
}

export function listDocuments(signal?: AbortSignal): Promise<DocumentListResponse> {
  return fetchJson<DocumentListResponse>(
    "/api/documents",
    "작업 목록을 불러올 수 없어.",
    signal,
  );
}

export function getDocumentProcessing(
  documentId: string,
  signal?: AbortSignal,
): Promise<DocumentProcessing> {
  return fetchJson<DocumentProcessing>(
    `/api/documents/${encodeURIComponent(documentId)}/processing`,
    "처리 상태를 불러올 수 없어.",
    signal,
  );
}

export async function deleteDocument(documentId: string, signal?: AbortSignal): Promise<void> {
  const response = await fetch(
    buildApiUrl(`/api/documents/${encodeURIComponent(documentId)}`),
    {
      method: "DELETE",
      cache: "no-store",
      signal,
    },
  );

  if (!response.ok) {
    throw new ApiRequestError(await readErrorMessage(response, "문서를 삭제할 수 없어."), response.status);
  }
}

export function getDocumentSummary(documentId: string, signal?: AbortSignal): Promise<DocumentSummary> {
  return fetchJson<DocumentSummary>(
    `/api/documents/${encodeURIComponent(documentId)}/summary`,
    "문서 요약을 불러올 수 없어.",
    signal,
  );
}

export function getPageResult(
  documentId: string,
  pageNumber: number,
  signal?: AbortSignal,
): Promise<PageData> {
  return fetchJson<PageData>(
    `/api/documents/${encodeURIComponent(documentId)}/pages/${pageNumber}`,
    "페이지 결과를 불러올 수 없어.",
    signal,
  );
}

export function getSelectionExplanationHistory(
  documentId: string,
  pageNumber: number,
  signal?: AbortSignal,
): Promise<SelectionExplanationHistory> {
  return fetchJson<SelectionExplanationHistory>(
    `/api/documents/${encodeURIComponent(documentId)}/pages/${pageNumber}/selection-explanations`,
    "선택 설명 기록을 불러올 수 없어.",
    signal,
  );
}

export async function updateSelectionExplanationState(
  documentId: string,
  pageNumber: number,
  selectionId: string,
  patch: { is_important?: boolean },
  signal?: AbortSignal,
): Promise<SelectionExplanationState> {
  return fetchJson<SelectionExplanationState>(
    `/api/documents/${encodeURIComponent(documentId)}/pages/${pageNumber}/selection-explanations/${encodeURIComponent(
      selectionId,
    )}`,
    "선택 설명 상태를 저장할 수 없어.",
    undefined,
    {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(patch),
      signal,
    },
  );
}

export async function deleteSelectionExplanation(
  documentId: string,
  pageNumber: number,
  selectionId: string,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(
    buildApiUrl(
      `/api/documents/${encodeURIComponent(documentId)}/pages/${pageNumber}/selection-explanations/${encodeURIComponent(
        selectionId,
      )}`,
    ),
    {
      method: "DELETE",
      cache: "no-store",
      signal,
    },
  );

  if (!response.ok) {
    throw new ApiRequestError(await readErrorMessage(response, "선택 설명을 삭제할 수 없어."), response.status);
  }
}

export async function createSelectionExplanation(
  documentId: string,
  pageNumber: number,
  selectedBbox: [number, number, number, number],
  signal?: AbortSignal,
): Promise<SelectionExplanation> {
  const path = `/api/documents/${encodeURIComponent(documentId)}/pages/${pageNumber}/selection-explanation`;
  const fallbackMessage = "선택 영역 설명을 생성할 수 없어.";
  const requestInit = {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ selected_bbox: selectedBbox }),
    signal,
  };

  try {
    return await fetchJson<SelectionExplanation>(
      path,
      fallbackMessage,
      undefined,
      requestInit,
    );
  } catch (error) {
    if (!isTransientApiError(error) || signal?.aborted) {
      throw error;
    }

    await wait(1400, signal);
    return fetchJson<SelectionExplanation>(
      path,
      fallbackMessage,
      undefined,
      {
        ...requestInit,
        body: JSON.stringify({ selected_bbox: selectedBbox }),
      },
    );
  }
}

export async function createSelectionFollowUp(
  documentId: string,
  pageNumber: number,
  selectionId: string,
  question: string,
  signal?: AbortSignal,
): Promise<SelectionFollowUp> {
  return fetchJson<SelectionFollowUp>(
    `/api/documents/${encodeURIComponent(documentId)}/pages/${pageNumber}/selection-explanations/${encodeURIComponent(
      selectionId,
    )}/follow-up`,
    "추가 질문에 답할 수 없어.",
    undefined,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ question }),
      signal,
    },
  );
}

export async function postInteractionLog(
  payload: InteractionLogPayload,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(buildApiUrl("/api/logs"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
    signal,
  });

  if (!response.ok) {
    throw new ApiRequestError("로그 저장에 실패했어.", response.status);
  }
}

export async function uploadDocument(
  file: File,
  signal?: AbortSignal,
): Promise<DocumentUploadResponse> {
  const formData = new FormData();
  formData.append("file", file, file.name);

  const response = await fetch(buildApiUrl("/api/documents"), {
    method: "POST",
    body: formData,
    signal,
  });

  if (!response.ok) {
    throw new ApiRequestError(
      await readErrorMessage(response, "업로드에 실패했어."),
      response.status,
    );
  }

  try {
    return (await response.json()) as DocumentUploadResponse;
  } catch {
    throw new ApiRequestError("서버 오류가 발생했어.", response.status);
  }
}
