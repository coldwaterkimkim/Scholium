export type ResponseLanguage = "ko" | "en";

export const DEFAULT_RESPONSE_LANGUAGE: ResponseLanguage = "ko";
export const RESPONSE_LANGUAGE_STORAGE_KEY = "scholium.responseLanguage";

export const RESPONSE_LANGUAGE_OPTIONS: Array<{
  value: ResponseLanguage;
  label: string;
  shortLabel: string;
}> = [
  { value: "ko", label: "한국어", shortLabel: "KO" },
  { value: "en", label: "English", shortLabel: "EN" },
];

export function normalizeResponseLanguage(value: unknown): ResponseLanguage {
  return value === "en" ? "en" : DEFAULT_RESPONSE_LANGUAGE;
}

export function getStoredResponseLanguage(): ResponseLanguage {
  if (typeof window === "undefined") {
    return DEFAULT_RESPONSE_LANGUAGE;
  }

  return normalizeResponseLanguage(window.localStorage.getItem(RESPONSE_LANGUAGE_STORAGE_KEY));
}

export function setStoredResponseLanguage(language: ResponseLanguage): void {
  if (typeof window === "undefined") {
    return;
  }

  window.localStorage.setItem(RESPONSE_LANGUAGE_STORAGE_KEY, language);
}

