import type { FinalAnchor } from "@/lib/api";
import { normalizedBboxToPixelRect, type ImageDisplayMetrics } from "@/utils/bbox";

import styles from "./AnchorOverlay.module.css";

// Legacy/debug-only overlay for precomputed final anchors.
// The primary MVP interaction is now drag-based selected-region explanation in DocumentViewer.
type AnchorOverlayProps = {
  anchors: FinalAnchor[];
  imageDisplayMetrics: ImageDisplayMetrics | null;
  selectedAnchorId: string | null;
  onSelectAnchor: (anchorId: string) => void;
};

export function AnchorOverlay({
  anchors,
  imageDisplayMetrics,
  selectedAnchorId,
  onSelectAnchor,
}: AnchorOverlayProps) {
  if (!imageDisplayMetrics || anchors.length === 0) {
    return null;
  }

  return (
    <div className={styles.overlay}>
      {anchors.map((anchor, index) => {
        const rect = normalizedBboxToPixelRect(anchor.bbox, imageDisplayMetrics);
        const isSelected = anchor.anchor_id === selectedAnchorId;

        return (
          <div key={anchor.anchor_id} className={styles.anchorLayer}>
            <div
              className={`${styles.outline} ${isSelected ? styles.outlineSelected : ""}`}
              style={{
                left: `${rect.left}px`,
                top: `${rect.top}px`,
                width: `${rect.width}px`,
                height: `${rect.height}px`,
              }}
            />
            <button
              type="button"
              className={`${styles.marker} ${isSelected ? styles.markerSelected : ""}`}
              style={{
                left: `${rect.left + 8}px`,
                top: `${rect.top + 8}px`,
              }}
              onPointerDown={(event) => event.stopPropagation()}
              onClick={() => onSelectAnchor(anchor.anchor_id)}
              aria-label={`${index + 1}. ${anchor.label}`}
              aria-pressed={isSelected}
            >
              {index + 1}
            </button>
          </div>
        );
      })}
    </div>
  );
}
