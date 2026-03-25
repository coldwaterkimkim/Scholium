export type NormalizedBBox = [number, number, number, number];

export type ImageDisplayMetrics = {
  width: number;
  height: number;
  offsetLeft: number;
  offsetTop: number;
};

export type PixelRect = {
  left: number;
  top: number;
  width: number;
  height: number;
};

function clamp(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) {
    return min;
  }

  return Math.min(Math.max(value, min), max);
}

export function normalizedBboxToPixelRect(
  bbox: NormalizedBBox,
  imageDisplayMetrics: ImageDisplayMetrics,
): PixelRect {
  const [x, y, width, height] = bbox;

  const safeImageWidth = Math.max(1, imageDisplayMetrics.width);
  const safeImageHeight = Math.max(1, imageDisplayMetrics.height);

  const clampedX = clamp(x, 0, 1);
  const clampedY = clamp(y, 0, 1);
  const clampedWidth = clamp(width, 0, 1 - clampedX);
  const clampedHeight = clamp(height, 0, 1 - clampedY);

  const rawLeft = imageDisplayMetrics.offsetLeft + clampedX * safeImageWidth;
  const rawTop = imageDisplayMetrics.offsetTop + clampedY * safeImageHeight;
  const rawWidth = Math.max(1, clampedWidth * safeImageWidth);
  const rawHeight = Math.max(1, clampedHeight * safeImageHeight);

  const maxLeft = imageDisplayMetrics.offsetLeft + safeImageWidth - 1;
  const maxTop = imageDisplayMetrics.offsetTop + safeImageHeight - 1;

  const left = clamp(rawLeft, imageDisplayMetrics.offsetLeft, maxLeft);
  const top = clamp(rawTop, imageDisplayMetrics.offsetTop, maxTop);
  const maxWidth = imageDisplayMetrics.offsetLeft + safeImageWidth - left;
  const maxHeight = imageDisplayMetrics.offsetTop + safeImageHeight - top;

  return {
    left,
    top,
    width: clamp(rawWidth, 1, Math.max(1, maxWidth)),
    height: clamp(rawHeight, 1, Math.max(1, maxHeight)),
  };
}
