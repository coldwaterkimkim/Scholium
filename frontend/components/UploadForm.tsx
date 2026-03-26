"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

import { ApiRequestError, uploadDocument } from "@/lib/api";

import styles from "./UploadForm.module.css";

function getUploadErrorMessage(error: unknown): string {
  if (error instanceof ApiRequestError) {
    return error.message;
  }

  if (error instanceof Error && error.message) {
    return error.message;
  }

  return "업로드에 실패했어.";
}

export function UploadForm() {
  const router = useRouter();
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!selectedFile) {
      setError("PDF 파일을 먼저 선택해.");
      return;
    }

    const fileName = selectedFile.name.toLowerCase();
    if (!fileName.endsWith(".pdf")) {
      setError("PDF 파일만 업로드할 수 있어.");
      return;
    }

    setError(null);
    setIsSubmitting(true);

    try {
      const result = await uploadDocument(selectedFile);
      router.push(`/documents/${encodeURIComponent(result.document_id)}/processing`);
    } catch (uploadError: unknown) {
      setError(getUploadErrorMessage(uploadError));
      setIsSubmitting(false);
    }
  }

  function handleFileChange(event: React.ChangeEvent<HTMLInputElement>) {
    const nextFile = event.target.files?.[0] ?? null;
    setSelectedFile(nextFile);
    setError(null);
  }

  return (
    <div className={styles.page}>
      <main className={styles.shell}>
        <section className={styles.surface}>
          <div className={styles.header}>
            <h1 className={styles.title}>Scholium 내부 테스트 업로드</h1>
            <p className={styles.description}>
              PDF 1개를 올리면 자동으로 render, pass1, synthesis, pass2가 순서대로 실행돼.
            </p>
          </div>

          <form className={styles.form} onSubmit={handleSubmit}>
            <label className={styles.fileField}>
              <span className={styles.fieldLabel}>PDF 파일</span>
              <input
                type="file"
                accept="application/pdf,.pdf"
                onChange={handleFileChange}
                disabled={isSubmitting}
              />
            </label>

            <div className={styles.fileInfo}>
              {selectedFile ? selectedFile.name : "아직 선택된 파일이 없어."}
            </div>

            {error ? <div className={styles.errorBox}>{error}</div> : null}

            <button type="submit" className={styles.submitButton} disabled={isSubmitting}>
              {isSubmitting ? "업로드 중..." : "업로드하고 처리 시작"}
            </button>
          </form>
        </section>
      </main>
    </div>
  );
}
