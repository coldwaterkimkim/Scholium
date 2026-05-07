# Scholium 성능/비용 진단 보고서

작성 시점: 2026-03-27  
대상 문서: `doc_344e491d3c6a48e6b1592674e26c982e`

## 1. 한 줄 결론

지금 시간이 너무 오래 걸리고 비용이 크게 나오는 핵심 이유는, 이 프로젝트가 본질적으로 **페이지 단위 멀티모달 호출을 두 번(pass1, pass2) 반복하는 구조**이기 때문이야.  
그리고 이번 실패 문서는 그 위에 **pass1 단계에서 `Connection error`가 다수 발생**해서, 긴 시간 동안 시도는 계속했지만 usable page 수가 부족해져서 최종적으로 **document synthesis threshold 미달로 실패**한 케이스야.

즉, 이번 문제는 두 층으로 나뉜다.

1. **이번 문서의 직접 실패 원인**  
   pass1 다수 실패 -> usable coverage 부족 -> synthesis 실패
2. **프로젝트 전체의 구조적 비용/지연 원인**  
   `pass1 N회 + synthesis 1회 + pass2 M회` 멀티모달 호출 구조, 높은 reasoning effort, 재시도 중첩, 큰 이미지 입력

---

## 2. 실제로 확인된 사실

### 2.1 현재 processing 상태

`GET /api/documents/doc_344e491d3c6a48e6b1592674e26c982e/processing`

확인 결과:

- `status = failed`
- `stage = synthesis`
- `total_pages = 23`
- `rendered_pages = 23`
- `pass1_completed_pages = 3`
- `pass1_failed_pages = 20`
- `pass1_processed_pages = 23`
- `synthesis_ready = false`
- `ready_for_viewer = false`
- `error_message = "Document synthesis failed. ... usable=3 ... coverage_threshold=17"`

의미:

- 렌더는 정상 완료
- pass1은 전 페이지를 다 시도했지만, usable page가 3장밖에 안 남음
- synthesis는 `23장 중 최소 17장` usable pass1이 필요했는데 3장이라 실패

### 2.2 실제 DB 상태

SQLite 확인 결과:

- 문서 row
  - `status = failed`
  - `total_pages = 23`
  - `error_message = Document synthesis failed... coverage_threshold=17`
- 페이지 row
  - `pass1_status = completed` 3개
  - `pass1_status = failed` 20개
  - 실패 요약은 대부분  
    `Pass1 failed. Responses API call failed for stage 'pass1': Connection error.`

의미:

- 실제 실패의 직접 원인은 대부분 `pass1` 단계의 connection failure야.
- 이 문서는 render 문제도 아니고 pass2 문제도 아니야.

### 2.3 JSON artifact와 DB 사이 드리프트도 존재

실제 `data/analysis/doc_344.../pages/*/page_analysis_pass1.json` 파일은 12개가 남아 있었어.  
그런데 현재 DB에서 `pass1_status = completed`는 3개뿐이야.

이건 보통 아래 중 하나를 뜻해.

- 과거 시도에서 일부 페이지는 성공했는데, 이후 재실행 과정에서 DB 상태가 다시 실패로 덮였음
- 또는 recovery/retry 흐름에서 artifact는 남고 상태는 달라짐

이 드리프트는 **실제 소모된 API 작업량이 최종 DB snapshot보다 더 많았을 수 있다**는 뜻이야.  
즉 “최종 완료 페이지가 3개니까 비용도 적게 나왔겠지”라고 보면 안 돼.

---

## 3. 왜 이렇게 오래 걸렸는가

## 3.1 가장 큰 원인: 페이지별 멀티모달 호출 자체

현재 파이프라인은:

1. render
2. pass1
3. document synthesis
4. pass2

여기서 시간이 오래 걸리는 건 render가 아니라 거의 전부 `pass1`/`pass2`야.

이유:

- `pass1`: 렌더된 각 페이지마다 멀티모달 Responses API 호출 1회 이상
- `pass2`: pass1 완료 페이지마다 멀티모달 Responses API 호출 1회 이상
- 모델 기본값이 `gpt-5.4`
- `pass1`, `pass2` 둘 다 reasoning effort가 `high`

즉 23페이지 문서는 아주 단순 계산으로도 `pass1`만 최소 23회 수준의 모델 작업을 만든다.

## 3.2 두 번째 원인: 동시성이 고정 3

지금 코드 기준:

- `pass1 max_workers = 3`
- `pass2 max_workers = 3`

즉 23페이지 문서는 대략 `ceil(23 / 3)` 배치로 돌아가.  
한 페이지라도 오래 걸리거나 재시도되면 전체 wall-clock이 바로 늘어난다.

## 3.3 세 번째 원인: 실패가 “빨리 끝나는 실패”가 아님

이번 문서의 핵심 실패는 `Connection error`였어.  
이 오류는 즉시 fail-fast가 아니라, SDK retry, 호출 대기, 내부 재시도 흐름을 거치면서 시간이 소비됐을 가능성이 높아.

특히 현재 설정은:

- `OPENAI_TIMEOUT_SECONDS = 60`
- `OPENAI_MAX_RETRIES = 2`

이 설정 때문에 한 페이지가 문제일 때 **짧게 1초 만에 실패하는 구조가 아니라, 분 단위로 시간을 잡아먹는 구조**가 되기 쉽다.

## 3.4 네 번째 원인: synthesis가 늦게 실패함

document synthesis는 pass1 coverage가 충분히 확보된 뒤에야 의미 있게 진행돼.

현재 규칙:

- `coverage_threshold = max(3, ceil(total_rendered_pages * 0.7))`

23페이지 문서는 threshold가 17이야.

즉 많은 페이지를 pass1에서 이미 시도한 뒤에야 “이 문서는 threshold를 못 넘겠네”가 명확해진다.  
이건 곧 **비용도 이미 많이 쓰고, 시간도 이미 많이 쓴 뒤에 실패하는 구조**라는 뜻이야.

---

## 4. 왜 실패했는가

## 4.1 직접 원인

이번 문서는 `pass1` 단계에서 connection failure가 누적돼서 usable pass1 결과가 3개밖에 남지 않았고, 그 결과 synthesis가 실패했어.

요약하면:

`render 성공 -> pass1 다수 connection error -> usable pass1 = 3/23 -> threshold 17 미달 -> synthesis failed`

## 4.2 더 정확히 말하면

이 실패는 “문서 내용이 너무 어려워서 AI가 분석을 못 했다”보다,

- 네트워크/SDK/OpenAI 연결 계층 실패가 다수 발생했고
- 그 실패를 구조적으로 흡수하지 못했으며
- synthesis threshold가 그 상태를 최종 실패로 닫았다는 쪽에 가까워.

## 4.3 내가 확실하게 말할 수 없는 부분

`Connection error`의 하위 원인이 정확히 무엇인지는 현재 저장 정보만으로는 확정 못 해.

왜냐하면 지금 시스템은 raw traceback이나 세부 네트워크 로그를 저장하지 않고, 짧은 요약만 남기기 때문이야.

즉 아래 후보 중 하나일 수는 있지만, 현재 증거만으로 단정하긴 어려워:

- OpenAI API 일시적 연결 불안정
- 로컬 네트워크/인터넷 불안정
- SDK 레벨 connection reset/timeout
- 동시 요청/재시도 중 연결 품질 저하

그래도 **“pass1 connection error 누적” 자체는 확실한 사실**이야.

---

## 5. 왜 비용이 이렇게 많이 나오는가

## 5.1 전체 구조가 원래 비싼 구조다

이 프로젝트는 기본적으로 한 문서를 다음처럼 분해해:

- pass1 = 페이지 수만큼 호출
- synthesis = 문서당 1회 호출
- pass2 = 페이지 수 또는 pass1 완료 페이지 수만큼 호출

예를 들어 23페이지 문서를 이상적으로만 계산해도:

- pass1 최소 23회
- synthesis 1회
- pass2 최소 23회

즉 **최소 47회 수준의 모델 작업**이 될 수 있어.

여기에 retry가 붙으면 훨씬 커진다.

## 5.2 pass1, pass2 둘 다 `gpt-5.4 + high reasoning`

현재 기본값:

- pass1 = `gpt-5.4`, `high`
- synthesis = `gpt-5.4`, `medium`
- pass2 = `gpt-5.4`, `high`

즉 가장 비싼 쪽에 가까운 모델/effort 조합을 문서 전반에 넓게 깔아놓은 구조야.

이건 품질은 높일 수 있어도, MVP 실험에서는 비용 민감도가 매우 높다.

## 5.3 이미지 입력이 크다

render 기본값:

- PNG
- RGB
- long edge 1600px

이 이미지를 매번 base64 data URL로 넣어서 전송해.

중요한 점:

- base64 문자열 길이 자체가 그대로 텍스트 토큰 과금이 된다고 단정하면 안 돼
- 하지만 **이미지 해상도/크기 자체는 멀티모달 입력 비용과 전송 부담을 키우는 핵심 요소**야
- 그리고 base64 인라인은 네트워크 payload를 확실히 키운다

실제 샘플 페이지는 대략 50KB~100KB대 PNG가 확인됐고, 페이지에 따라 더 커질 수 있어.

## 5.4 retry가 겹친다

현재 비용 증폭 포인트:

1. OpenAI SDK retry
2. JSON parse/schema failure 시 repair retry
3. pass2 timeout retry
4. pass2 diversity retry

특히 pass2는 논리적으로 **페이지당 최대 여러 번 재호출**이 가능해.

즉 “23페이지니까 23번쯤이겠지”가 아니라,
실제로는 **페이지 수 x 재시도 층수**가 돼버릴 수 있어.

## 5.5 현재 문서는 final snapshot보다 더 많은 비용이 들었을 가능성이 높다

이 문서는 DB 기준으로는 pass1 완료 3장뿐이지만,
artifact 기준으로는 pass1 결과 파일이 12장 남아 있어.

즉 이미 어떤 시점에는 더 많은 페이지가 성공했거나, 적어도 더 많은 작업이 실제로 수행됐다는 뜻이야.  
그래서 **최종 상태만 보고 비용을 과소평가하면 안 된다.**

---

## 6. 비용을 줄이면서 성능을 최대한 유지하려면

아래는 “품질을 완전히 버리지 않고” 비용과 시간을 줄이는 순서야.

## 6.1 1순위: pass1을 더 싼 기본 설정으로 내리고, 어려운 페이지만 승격

추천 방향:

- pass1 기본 reasoning `high -> medium` 또는 `low`
- 가능하면 pass1만 더 작은 모델로 분리
- 후보 수가 너무 적거나 타입 다양성이 부족한 페이지에서만 상향 재시도

왜 이게 좋은가:

- pass1이 가장 넓은 fan-out이라 여기서 줄이는 효과가 제일 큼
- 모든 페이지를 최고 사양으로 돌리는 게 아니라, 일부 페이지만 비싼 처리로 보내면 품질 손실을 통제하기 쉬움

예상 효과:

- 비용 크게 감소
- 처리 시간도 같이 감소
- 제품 품질 저하는 제한적일 가능성이 높음

## 6.2 2순위: pass2 diversity retry를 기본 비활성화

현재 pass2는 anchor type이 한쪽으로 몰리면 모델을 다시 부를 수 있어.

이건 품질 개선 의도는 좋지만, MVP 기준으로는 비용 대비 효과가 불확실해.

추천 방향:

- diversity retry를 모델 재호출이 아니라 local rerank/post-process로 대체
- 또는 기본은 warning만 남기고 재호출은 하지 않기

왜 이게 좋은가:

- pass2는 이미 무거운 입력을 가진 상태라 재호출 1번의 체감 비용이 큼

## 6.3 3순위: render 입력을 줄이기

추천 방향:

- `1600px -> 1200px` 또는 `1024px`
- `PNG -> JPEG` 검토
- 더 나아가면 `pass1용 저해상도`, `pass2용 중간 해상도` 분리

왜 이게 좋은가:

- 멀티모달 입력 비용/전송 크기/처리 시간을 같이 줄일 수 있음

주의:

- 표, 작은 글자, 복잡한 다이어그램 인식 품질은 같이 확인해야 함

## 6.4 4순위: pass2를 전 페이지 선계산하지 않기

현재 구조는 문서가 viewer-ready 되기 전에 pass2를 광범위하게 수행하는 편이야.

추천 방향:

- 어려운 페이지
- synthesis가 중요하다고 본 페이지
- 실제 사용자가 연 페이지

위주로 먼저 처리하고 나머지는 지연 처리

왜 이게 좋은가:

- 즉시 필요한 가치만 먼저 제공
- 전체 비용과 완료 시간을 동시에 줄임

이건 구조 변경 폭이 조금 더 있지만, **비용 절감 효과는 매우 클 가능성**이 있어.

## 6.5 5순위: retry 층 얇게 만들기

추천 방향:

- `OPENAI_MAX_RETRIES`를 2에서 1로 낮추기 검토
- pass2 timeout retry는 유지하되, diversity retry는 줄이기
- stage별로 retry 정책 다르게 두기

왜 이게 좋은가:

- 현재는 재시도 층이 겹쳐서 느리고 비싸다
- 모든 retry가 실제 품질 향상으로 이어지는 건 아님

## 6.6 6순위: synthesis threshold를 MVP 목적에 맞게 완화

현재 23페이지 문서는 최소 17페이지 usable pass1이 필요해.

이건 내부 테스트/빠른 검증 기준에서는 꽤 빡빡해.

추천 방향:

- threshold 완화
- 또는 “partial viewer mode” 허용

왜 이게 좋은가:

- 이미 돈과 시간을 쓴 문서를 너무 늦게 실패시키는 문제를 줄일 수 있음

주의:

- 이건 비용 절감보다 “낭비 방지”에 더 가까운 조치야.

## 6.7 7순위: storage 재검증/재로딩 줄이기

현재 구조는 저장 시 temp file write -> reread -> revalidate를 하고, 읽을 때도 다시 validation을 많이 해.

추천 방향:

- 한 문서 처리 중에는 summary/pass1 artifact를 메모리 캐시
- 쓰기 시 검증 1회, 읽기 시 신뢰 범위 확대

효과:

- OpenAI 비용보다 impact는 작지만
- 전체 latency와 tail latency를 줄여줌

---

## 7. 우선순위 제안

내 판단 기준으로는 이 순서가 가장 실용적이야.

1. **pass1 기본 모델/effort 다운그레이드**
2. **pass2 diversity retry 제거 또는 약화**
3. **render 해상도 축소**
4. **pass2를 선택적/지연 계산으로 전환**
5. **retry 층 축소**
6. **synthesis threshold 완화 또는 partial viewer 허용**
7. **storage 캐시/재검증 최적화**

이 순서는 이유가 명확해:

- 먼저 가장 큰 fan-out 단계에서 단가를 줄이고
- 그 다음 불필요한 재호출을 줄이고
- 그 다음 입력 크기를 줄이고
- 마지막에 구조적 낭비를 줄이는 게 효과 대비 안전하다

---

## 8. 내가 확신하는 것 / 아직 불확실한 것

## 확신하는 것

- 이번 문서는 render가 아니라 pass1 connection failure 누적으로 실패했다
- synthesis는 coverage threshold 미달로 실패했다
- 프로젝트 전체에서 비용의 중심은 pass1/pass2 페이지 fan-out이다
- `gpt-5.4 + high reasoning + large image + retries` 조합은 MVP에 매우 비싸다

## 불확실한 것

- `Connection error`의 정확한 하위 원인
- 이미지 입력 비용이 전체 청구액에서 차지하는 정확한 비중
- pass1/pass2 품질이 어느 수준까지 내려가도 제품 가치가 유지되는지

즉, 다음 단계는 “무조건 최적화”가 아니라:

1. 가장 큰 cost driver를 줄이는 가설 1~2개를 선택하고
2. 작은 테스트셋으로 품질 저하를 확인하는 방식이 맞다

---

## 9. 빠른 의사결정용 한 문장

지금 Scholium은 **고사양 모델을 페이지 단위로 너무 많이, 그리고 너무 여러 번 부르는 구조**라서 비싸고 느리다.  
이번 실패 문서는 그 구조 위에 pass1 connection error가 겹쳐서, 오래 기다린 뒤 synthesis threshold 미달로 실패한 케이스다.
