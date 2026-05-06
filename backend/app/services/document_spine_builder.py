from __future__ import annotations

from datetime import datetime, timezone

from app.models.parser import DocumentPageManifest, DocumentParseArtifact, PageManifestEntry
from app.models.pipeline_v2 import (
    DocumentSpineArtifact,
    HardPageCandidate,
    KeyPage,
    PageRoutingArtifact,
    PageRoutingEntry,
    PageRoutingMeta,
    PageRoutingResult,
    RecommendedExecution,
    RoutingSummary,
    SectionCluster,
)


_SCORE_ROUTE_SCAN_LIKE = 40
_SCORE_ROUTE_VISUAL_RICH = 30
_SCORE_TEXT_LENGTH_LT_80 = 20
_SCORE_TEXT_LENGTH_LT_200 = 10
_SCORE_NON_EMPTY_TEXT_BLOCKS_LE_1 = 20
_SCORE_NON_EMPTY_TEXT_BLOCKS_LE_3 = 10
_SCORE_HAS_TABLE = 25
_SCORE_HAS_FIGURE = 15
_SCORE_IMAGE_COUNT_GE_3 = 20
_SCORE_IMAGE_COUNT_GE_1 = 10
_SCORE_OCR_USED = 25
_HARD_PAGE_CANDIDATE_THRESHOLD = 50
_MAX_KEY_PAGES = 5


class DocumentSpineBuilder:
    def build(
        self,
        document_id: str,
        parse_artifact: DocumentParseArtifact,
        page_manifest: DocumentPageManifest,
        *,
        pipeline_mode: str,
        spine_mode: str,
        schema_version: str,
    ) -> tuple[DocumentSpineArtifact, PageRoutingArtifact]:
        generated_at = datetime.now(timezone.utc).isoformat()
        sorted_pages = sorted(page_manifest.pages, key=lambda page: page.page_number)

        page_routing_entries = [self._build_page_routing_entry(page) for page in sorted_pages]
        routing_summary = self._build_routing_summary(sorted_pages, page_routing_entries)
        section_clusters = self._build_section_clusters(sorted_pages)
        hard_page_candidates = [
            HardPageCandidate(
                page_number=entry.page_number,
                hard_page_score=entry.hard_page_score,
                hard_page_reasons=list(entry.hard_page_reasons),
                recommended_execution=entry.recommended_execution,
            )
            for entry in page_routing_entries
            if entry.hard_page_score >= _HARD_PAGE_CANDIDATE_THRESHOLD
        ]
        key_pages = self._build_key_pages(section_clusters, page_routing_entries)

        document_spine = DocumentSpineArtifact.model_validate(
            {
                "meta": {
                    "schema_version": schema_version,
                    "generated_at": generated_at,
                    "pipeline_mode": pipeline_mode,
                    "spine_mode": spine_mode,
                    "parser_source": page_manifest.parser_source or parse_artifact.parser_source,
                },
                "result": {
                    "document_id": document_id,
                    "total_pages": len(parse_artifact.pages),
                    "section_clusters": [cluster.model_dump(mode="json") for cluster in section_clusters],
                    "key_pages": [page.model_dump(mode="json") for page in key_pages],
                    "hard_page_candidates": [
                        candidate.model_dump(mode="json") for candidate in hard_page_candidates
                    ],
                    "routing_summary": routing_summary.model_dump(mode="json"),
                },
            }
        )
        page_routing = PageRoutingArtifact.model_validate(
            {
                "meta": PageRoutingMeta(
                    schema_version=schema_version,
                    generated_at=generated_at,
                    pipeline_mode=pipeline_mode,
                    spine_mode=spine_mode,
                ).model_dump(mode="json"),
                "result": PageRoutingResult(
                    document_id=document_id,
                    pages=page_routing_entries,
                ).model_dump(mode="json"),
            }
        )
        return document_spine, page_routing

    def _build_page_routing_entry(self, page: PageManifestEntry) -> PageRoutingEntry:
        hard_page_score, hard_page_reasons = self._score_hard_page(page)
        return PageRoutingEntry(
            page_number=page.page_number,
            base_route_label=page.route_label.value,
            base_route_reason=page.route_reason,
            hard_page_score=hard_page_score,
            hard_page_reasons=hard_page_reasons,
            recommended_execution=self._recommended_execution(page, hard_page_score),
        )

    def _score_hard_page(self, page: PageManifestEntry) -> tuple[int, list[str]]:
        score = 0
        reasons: list[str] = []

        if page.route_label.value == "scan-like":
            score += _SCORE_ROUTE_SCAN_LIKE
            reasons.append("base_route=scan-like")
        elif page.route_label.value == "visual-rich":
            score += _SCORE_ROUTE_VISUAL_RICH
            reasons.append("base_route=visual-rich")

        if page.text_length < 80:
            score += _SCORE_TEXT_LENGTH_LT_80
            reasons.append("text_length<80")
        elif page.text_length < 200:
            score += _SCORE_TEXT_LENGTH_LT_200
            reasons.append("text_length<200")

        if page.non_empty_text_block_count <= 1:
            score += _SCORE_NON_EMPTY_TEXT_BLOCKS_LE_1
            reasons.append("non_empty_text_block_count<=1")
        elif page.non_empty_text_block_count <= 3:
            score += _SCORE_NON_EMPTY_TEXT_BLOCKS_LE_3
            reasons.append("non_empty_text_block_count<=3")

        if page.has_table:
            score += _SCORE_HAS_TABLE
            reasons.append("has_table")
        if page.has_figure:
            score += _SCORE_HAS_FIGURE
            reasons.append("has_figure")
        if page.image_count >= 3:
            score += _SCORE_IMAGE_COUNT_GE_3
            reasons.append("image_count>=3")
        elif page.image_count >= 1:
            score += _SCORE_IMAGE_COUNT_GE_1
            reasons.append("image_count>=1")
        if page.ocr_used:
            score += _SCORE_OCR_USED
            reasons.append("ocr_used")

        return min(100, score), reasons

    def _recommended_execution(
        self,
        page: PageManifestEntry,
        hard_page_score: int,
    ) -> RecommendedExecution:
        if page.route_label.value == "scan-like":
            return RecommendedExecution.SELECTIVE_VISUAL_ENRICHMENT
        if page.has_table or page.has_figure or page.image_count >= 2:
            return RecommendedExecution.SELECTIVE_VISUAL_ENRICHMENT
        if page.route_label.value == "text-rich" and hard_page_score < 40:
            return RecommendedExecution.TEXT_FIRST
        return RecommendedExecution.MULTIMODAL

    def _build_routing_summary(
        self,
        pages: list[PageManifestEntry],
        page_routing_entries: list[PageRoutingEntry],
    ) -> RoutingSummary:
        return RoutingSummary(
            text_rich_pages=sum(1 for page in pages if page.route_label.value == "text-rich"),
            visual_rich_pages=sum(1 for page in pages if page.route_label.value == "visual-rich"),
            scan_like_pages=sum(1 for page in pages if page.route_label.value == "scan-like"),
            hard_page_count=sum(
                1
                for entry in page_routing_entries
                if entry.hard_page_score >= _HARD_PAGE_CANDIDATE_THRESHOLD
            ),
        )

    def _build_section_clusters(self, pages: list[PageManifestEntry]) -> list[SectionCluster]:
        if not pages:
            return []

        # This is intentionally a cheap heuristic cluster, not a semantic section graph.
        clusters: list[SectionCluster] = []
        current_pages = [pages[0]]
        cluster_index = 1

        for page in pages[1:]:
            if page.route_label == current_pages[-1].route_label:
                current_pages.append(page)
                continue
            clusters.append(self._create_section_cluster(cluster_index, current_pages))
            cluster_index += 1
            current_pages = [page]

        clusters.append(self._create_section_cluster(cluster_index, current_pages))
        return clusters

    def _create_section_cluster(
        self,
        cluster_index: int,
        pages: list[PageManifestEntry],
    ) -> SectionCluster:
        page_numbers = [page.page_number for page in pages]
        first_page = pages[0]
        return SectionCluster(
            cluster_id=f"cluster_{cluster_index}",
            start_page=page_numbers[0],
            end_page=page_numbers[-1],
            page_numbers=page_numbers,
            dominant_route_label=first_page.route_label.value,
            cluster_reason=first_page.route_reason,
        )

    def _build_key_pages(
        self,
        section_clusters: list[SectionCluster],
        page_routing_entries: list[PageRoutingEntry],
    ) -> list[KeyPage]:
        if not page_routing_entries:
            return []

        ordered_candidates: list[tuple[int, str]] = [
            (page_routing_entries[0].page_number, "document_start")
        ]
        ordered_candidates.extend(
            (cluster.start_page, "cluster_start")
            for cluster in section_clusters
        )
        ordered_candidates.extend(
            (entry.page_number, "high_hard_page_score")
            for entry in sorted(
                page_routing_entries,
                key=lambda entry: (-entry.hard_page_score, entry.page_number),
            )
        )

        key_pages: list[KeyPage] = []
        seen_pages: set[int] = set()
        for page_number, reason in ordered_candidates:
            if page_number in seen_pages:
                continue
            seen_pages.add(page_number)
            key_pages.append(KeyPage(page_number=page_number, reason=reason))
            if len(key_pages) >= _MAX_KEY_PAGES:
                break

        return key_pages
