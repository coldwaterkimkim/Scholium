# Scholium Competitor Matrix

작성 기준:
- 이 문서는 Scholium repo와 기존 PRD에서 확인되는 제품 정의를 기준으로 만든 사업계획서용 비교표다.
- Scholium은 현재 구현된 기능 기준으로 적었다.
- 경쟁/대체재는 "특정 회사의 최신 기능 비교"가 아니라, 사용자가 실제로 대체할 수 있는 제품 유형 기준으로 적었다.
- 주요 근거:
  - `docs/scholium_product_prd_v0_revised.md`
  - `docs/bizplan_product_fact_pack.md`
  - `frontend/components/DocumentViewer.tsx`
  - `frontend/components/AnchorOverlay.tsx`
  - `frontend/components/RightPanel.tsx`
  - `backend/app/api/documents.py`
  - `backend/app/services/pass2_refiner.py`
  - `backend/app/services/pass2_compat_builder.py`

## 1. 비교 대상

- 범용 AI 챗봇
  - 예: ChatGPT, Claude, Perplexity 같은 일반 질의형 AI
- PDF 문서 챗봇
  - 예: PDF 업로드 후 문서 전체에 질문하는 문서 Q&A형 도구
- 전사본 기반 학습 서비스
  - 예: 강의 전사, 회의 전사, 자동 노트 기반 서비스
- 일반 PDF 뷰어 / LMS 자료 열람
  - 예: PDF reader, 강의자료 다운로드/열람형 서비스
- Scholium

## 2. 비교 축 기준

- 입력 방식: 사용자가 무엇을 넣고 시작하는가
- 인터랙션 위치: 학습 중 상호작용이 자료 내부에서 일어나는가, 외부 채팅창에서 일어나는가
- 문맥 유지 여부: 현재 보고 있는 페이지 문맥을 유지한 채 이해를 이어갈 수 있는가
- 현재 페이지 요소 해설 가능 여부: 지금 보고 있는 제목, 도식, 문장, 특정 포인트를 중심으로 설명이 붙는가
- PDF 내부 anchored explanation 가능 여부: PDF 내부 특정 위치에 anchor를 붙여 설명할 수 있는가
- 학습 흐름 유지 강도: 자료 밖으로 벗어나지 않게 붙잡는 힘이 강한가

## 3. 비교 매트릭스

| 비교 대상 | 입력 방식 | 인터랙션 위치 | 문맥 유지 여부 | 현재 페이지 요소 해설 가능 여부 | PDF 내부 anchored explanation 가능 여부 | 학습 흐름 유지 강도 |
| --- | --- | --- | --- | --- | --- | --- |
| 범용 AI 챗봇 | 사용자가 질문을 직접 입력하고 필요하면 문서 내용을 복붙하거나 파일을 첨부 | 외부 채팅창 | 약함 | 제한적 | 없음 | 약함 |
| PDF 문서 챗봇 | PDF 업로드 후 질문 입력 | 대체로 외부 채팅창 또는 문서 옆 채팅창 | 중간 | 제한적 | 보통 없음 | 중간 |
| 전사본 기반 학습 서비스 | 음성/영상/강의 녹음 또는 전사본 입력 | 전사본/요약/노트 화면 | 약함 | 약함 | 없음 | 약함 |
| 일반 PDF 뷰어 / LMS 자료 열람 | PDF 파일 또는 강의자료 열기 | 자료 내부이지만 해설 기능 없음 | 중간 | 없음 | 없음 | 약함 |
| Scholium | PDF 업로드 후 page-level 분석 실행 | PDF 페이지 내부 anchor + 우측 설명 패널 | 강함 | 가능 | 가능 | 강함 |

해석 메모:
- 범용 AI 챗봇은 설명 품질 자체가 좋을 수는 있지만, 질문이 자료 밖에서 이루어져 현재 페이지 문맥이 끊기기 쉽다.
- PDF 문서 챗봇은 문서 전체 Q&A에는 유용할 수 있지만, 현재 보고 있는 페이지의 특정 요소를 눌러 읽는 방식과는 다르다.
- 전사본 기반 서비스는 강의 구두 설명 복원에는 강점이 있을 수 있지만, 현재 페이지의 도식/문장/표 위치에 anchored explanation을 붙이는 방식은 아니다.
- 일반 PDF 뷰어 / LMS는 자료 열람에는 충분하지만, 막힘 해소용 해설 계층은 없다.

## 4. Scholium의 차별점 3개

- 첫째, 설명이 "문서 밖 채팅창"이 아니라 "현재 페이지 내부 anchor"에서 시작된다.
  - Scholium의 핵심 차이는 더 좋은 검색이 아니라, PDF 내부 특정 위치에 explanation을 붙여 현재 문맥을 유지하는 점이다.
- 둘째, 설명 단위가 문서 전체 요약이 아니라 page-level 막힘 포인트다.
  - 현재 viewer는 bbox anchor, short/long explanation, prerequisite, related pages를 함께 보여준다.
- 셋째, 학습 흐름 연결을 제품 구조에 넣었다.
  - page summary, page role, related pages, prerequisite를 함께 쓰기 때문에 단순 정의 설명보다 "왜 이 페이지가 지금 나오는지"를 붙이려는 구조다.

## 5. 과장 없이 인정해야 할 약점 3개

- 첫째, 범용 AI 챗봇은 여전히 강한 대체재다.
  - 질문 한 번으로 넓은 배경 설명을 빠르게 얻는 용도에서는 범용 AI가 더 편할 수 있다.
- 둘째, Scholium의 현재 viewer와 제품 표면은 아직 내부 데모 수준이다.
  - 문서 목록, 협업, auth, retry/cancel, zoom/pan, hover, 운영 화면 등은 아직 없다.
- 셋째, 품질은 문서 유형에 따라 편차가 있다.
  - selective pass2는 corpus 전체에서 절감 효과가 확인됐지만, `W1` 같은 outlier에서는 reduction이 거의 없었다.
  - compat도 latest assessment 기준 `safe but shallow` 한계가 남아 있다.

## 6. 사업계획서용 1문단 요약

Scholium의 차별점은 "문서를 올리면 답해주는 AI"가 아니라, PDF 내부 현재 페이지의 막힘 지점에 anchor를 붙이고 그 자리에서 short/long explanation, prerequisite, related page를 제공해 학습 흐름을 끊지 않는다는 점이다. 범용 AI 챗봇, PDF 문서 챗봇, 전사본 기반 서비스 모두 유용한 대체재이지만, 대부분 상호작용이 자료 밖 채팅창이나 전사본 화면에서 일어나기 때문에 현재 페이지 문맥을 유지한 채 이해를 이어가는 구조와는 다르다. 반대로 Scholium은 이 강점을 가지는 대신 viewer와 운영 표면은 아직 내부 데모 수준이고, 문서 유형에 따라 routing과 compat 품질 편차가 남아 있다. 따라서 사업계획서에서는 Scholium을 "범용 AI를 대체하는 서비스"가 아니라, PDF 기반 학습 자료에서 현재 페이지 문맥을 유지한 채 막힘을 해소하는 특화 인터페이스로 설명하는 것이 맞다.
