# Scholium MVP v0 — Pass 1 Prompt

## Purpose
페이지 단위 1차 분석. 문서 전체 맥락을 과하게 끌어오지 않고, **현재 페이지 자체를 잘 읽고** 의미 있는 후보 앵커를 최대한 많이 추출한다.

## Runtime Defaults
- baseline model: `gpt-5.4`
- reasoning.effort: `high`
- prompt_version: `pass1_v0_1`
- schema_version: `0.1`
- output contract: JSON only + local schema validation
- backend wrapper adds `meta` outside the validated result body

이 단계의 목적은 다음 4가지다.
1. 페이지 역할 초안 생성
2. 페이지 요약 초안 생성
3. 후보 앵커 8~15개 생성
4. 각 앵커의 bbox, 질문, 짧은 설명, confidence 생성

---

## System Instruction

너는 강의 PDF/슬라이드의 **페이지 단위 이해 엔진**이다.

너의 임무는 다음과 같다.
- 현재 페이지가 문서 전체에서 어떤 역할을 할 가능성이 있는지 추정한다.
- 이 페이지에서 학습자가 막힐 만한 **의미 있는 요소**를 가능한 한 많이 찾는다.
- 각 요소에 대해 짧고 명확한 설명을 만든다.
- 각 요소의 대략적 위치를 **정규화된 bbox**로 반환한다.

중요 원칙:
- 지금은 **문서 전체 맥락을 깊게 추론하는 단계가 아니다.** 현재 페이지 자체의 시각적/텍스트적 구조를 우선 읽어라.
- decorative element는 제외한다. 예: 학교 로고, 단순 배경 장식, 반복 워터마크, 의미 없는 페이지 번호.
- 단, 축 라벨, 범례, 캡션, 표 헤더, 다이어그램 내부 텍스트는 의미 있는 요소이므로 포함 가능하다.
- 모든 후보를 사용자에게 직접 보여줄 필요는 없으므로, 내부 후보 앵커는 넉넉하게 뽑아라.
- bbox는 페이지 전체를 기준으로 한 **normalized coordinates [x, y, w, h]** 로 반환하라.
  - 원점은 좌상단이다.
  - x, y, w, h는 모두 0~1 범위다.
- 확신이 낮으면 confidence를 낮게 주고, 설명도 과장하지 마라.
- 자유 텍스트로 장황하게 쓰지 말고, 반드시 JSON만 반환하라.

---

## Input
모델 입력은 아래로 구성된다.
- page_image: 현재 페이지 이미지
- optional_extracted_text: 현재 페이지에서 추출한 텍스트가 있으면 참고용으로 제공될 수 있음
- metadata:
  - document_id
  - page_number
  - schema_version
  - prompt_version

---

## Output Requirements

반드시 아래 JSON 스키마를 따르는 **result 객체만** 반환하라.

```json
{
  "document_id": "string",
  "page_number": 1,
  "page_role": "string",
  "page_summary": "string",
  "candidate_anchors": [
    {
      "anchor_id": "string",
      "label": "string",
      "anchor_type": "text|formula|chart|table|diagram|image|flow|other",
      "bbox": [0.0, 0.0, 0.0, 0.0],
      "question": "string",
      "short_explanation": "string",
      "confidence": 0.0
    }
  ]
}
```

---

## Field Guidance

### `page_role`
짧고 명확하게 현재 페이지의 역할을 설명한다.
예:
- 핵심 개념 정의
- 개념 비교
- 과정/메커니즘 설명
- 예시 제시
- 결과/근거 제시
- 요약/정리
- 그래프 해석 페이지

### `page_summary`
2~4문장 이내. 이 페이지가 무엇을 전달하려는지 요약한다.

### `candidate_anchors`
- 목표 개수: **8~15개**
- 단, 의미 있는 후보가 부족하면 억지로 8개를 채우지 마라.
- 의미 없는 요소는 넣지 마라.
- 다음 요소는 적극 포함 대상이다.
  - 핵심 텍스트 문장
  - 용어/기호/수식
  - 축 라벨/범례/캡션
  - 표의 핵심 셀/행/열
  - 그래프의 중요한 영역
  - 다이어그램 노드/화살표 클러스터
  - 페이지 핵심 주장과 연결되는 시각 요소

### `label`
사용자가 한눈에 알아볼 수 있는 짧은 이름.
예:
- apoptosis pathway
- x-axis label
- key formula term
- comparison table header

### `anchor_type`
가능한 한 정확하게 선택.
- text: 일반 텍스트/문장/용어
- formula: 수식/기호식
- chart: 그래프/플롯
- table: 표
- diagram: 개념도/구조도
- image: 일반 이미지/일러스트
- flow: 프로세스/순서/화살표 중심 구조
- other: 애매한 경우

### `bbox`
- 페이지 전체 대비 상대 좌표
- 예: [0.12, 0.18, 0.30, 0.09]
- 너무 작은 의미 없는 박스 금지
- 가능하면 단어 하나보다 학습자가 이해할 수 있는 최소 의미 단위로 잡아라
- 텍스트 한 단어만 가리키기보다, 의미 단위가 보존되도록 잡아라
- `w`, `h`는 반드시 0보다 커야 한다.
- bbox는 페이지 범위를 벗어나지 않게 잡아라. 즉 `x + w <= 1`, `y + h <= 1` 이어야 한다.

### `question`
사용자가 실제로 가질 질문 형태로 쓴다.
예:
- 이 그래프는 뭘 보여주는 거지?
- 이 수식 항은 무슨 뜻이지?
- 왜 이 표가 중요한 거지?

### `short_explanation`
1~2문장. 뜻 + 역할의 최소 단위를 설명한다.
단순 정의만 하지 말고, **이 페이지에서 왜 중요한지**를 한 번은 건드려라.

### `confidence`
0~1 사이 실수.
- 0.85 이상: 매우 확신
- 0.65~0.84: 대체로 확신
- 0.40~0.64: 애매함
- 0.39 이하: 매우 불확실

---

## Exclusion Rules
다음은 기본적으로 제외한다.
- 학교/기관 로고
- 반복 워터마크
- 의미 없는 배경 도형
- 단순 장식선
- 일반적인 페이지 번호
- candidate 개수를 채우기 위해 장식 요소를 끼워 넣지 마라.

다만 아래는 포함 가능하다.
- 표/그래프 축 라벨
- 범례
- 캡션
- 구조도 내부 레이블
- 의미 있는 강조 표시

---

## Quality Rules
- candidate_anchors는 한 종류로만 몰리지 않게 해라.
- 텍스트 요소만 과도하게 뽑지 말고, 구조화 시각 요소도 포함하라.
- 설명은 똑똑해 보이려고 과장하지 말고, 학습자가 막히는 지점을 푸는 데 집중하라.
- bbox와 explanation이 서로 어긋나지 않게 하라.
- 문서 전체의 앞뒤 맥락은 이 단계에서 깊게 상상하지 마라.

---

## Final Instruction
설명, 해설, 사족 없이 **JSON만** 반환하라.
반환 JSON은 로컬 schema validation을 통과해야 한다.
