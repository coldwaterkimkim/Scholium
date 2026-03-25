export type AnchorType =
  | "text"
  | "formula"
  | "chart"
  | "table"
  | "diagram"
  | "image"
  | "flow"
  | "other";

export type DocumentMeta = {
  document_id: string;
  filename: string;
  status: string;
  total_pages: number | null;
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

export type DocumentSummary = {
  document_id: string;
  overall_topic: string;
  overall_summary: string;
  sections: DocumentSection[];
  key_concepts: KeyConcept[];
  difficult_pages: number[];
  prerequisite_links: PrerequisiteLink[];
};

export type FinalAnchor = {
  anchor_id: string;
  label: string;
  anchor_type: AnchorType;
  bbox: [number, number, number, number];
  question: string;
  short_explanation: string;
  long_explanation: string;
  prerequisite: string;
  related_pages: number[];
  confidence: number;
};

export type InteractionEventType = "page_view" | "anchor_click" | "related_page_jump";

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
  final_anchors: FinalAnchor[];
  page_risk_note: string;
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

async function fetchJson<T>(path: string, fallbackMessage: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(buildApiUrl(path), {
    cache: "no-store",
    signal,
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

export function getDocument(documentId: string, signal?: AbortSignal): Promise<DocumentMeta> {
  return fetchJson<DocumentMeta>(
    `/api/documents/${encodeURIComponent(documentId)}`,
    "문서를 찾을 수 없어.",
    signal,
  );
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
