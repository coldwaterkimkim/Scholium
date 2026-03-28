# Performance Baseline

현재 Scholium baseline은 아래 조건에서 비교한다.

- parser backend: `DOCUMENT_PARSER_BACKEND`
- pass1 routing mode: `PASS1_ROUTING_MODE`
- pass1 / synthesis / pass2 model + reasoning effort
- global OpenAI timeout / max retries
- render image long edge

문서 1개를 처리하면 아래 artifact가 생성된다.

- `data/analysis/{document_id}/processing_benchmark.json`

이 artifact는 문서 단위로 아래를 기록한다.

- 전체 wall-clock 처리 시간
- render / parse / triage / pass1 / synthesis / pass2 stage 시간
- pass1 path 분포 (`text-first`, `multimodal`, `escalated`)
- pass2 성공 / 실패 페이지 수
- OpenAI 실제 `responses.create()` 시도 횟수

중요:

- OpenAI call count는 논리적 stage 수가 아니라 실제 API 시도 수다.
- local validation repair retry도 count에 포함된다.
- pass2 timeout retry도 count에 포함된다.
- `parse_time_seconds`, `triage_time_seconds`는 artifact 재사용이면 `0.0`이고, 대신 `parse_artifact_reused`, `page_manifest_reused`가 `true`로 남는다.

확인 명령:

```bash
cd backend
cat ../data/analysis/doc_xxx/processing_benchmark.json
```

짧게 보기:

```bash
cd backend
python3 - <<'PY'
import json
from pathlib import Path

document_id = "doc_xxx"
payload = json.loads((Path("../data/analysis") / document_id / "processing_benchmark.json").read_text())
for key in (
    "document_id",
    "final_status",
    "final_error_message",
    "total_processing_time_seconds",
    "render_time_seconds",
    "parse_time_seconds",
    "triage_time_seconds",
    "pass1_time_seconds",
    "synthesis_time_seconds",
    "pass2_time_seconds",
    "rendered_pages",
    "pass1_text_first_pages",
    "pass1_multimodal_pages",
    "pass1_escalated_pages",
    "openai_call_count_total",
):
    print(f"{key}: {payload.get(key)}")
PY
```

전/후 비교 예시:

```bash
python3 - <<'PY'
import json
from pathlib import Path

before = json.loads(Path("before_processing_benchmark.json").read_text())
after = json.loads(Path("after_processing_benchmark.json").read_text())

for key in (
    "total_processing_time_seconds",
    "pass1_time_seconds",
    "pass2_time_seconds",
    "openai_call_count_total",
    "openai_pass1_call_count",
    "pass1_text_first_pages",
    "pass1_multimodal_pages",
):
    print(key, before.get(key), "->", after.get(key))
PY
```

비교 원칙:

- 가능하면 같은 PDF를 같은 환경에서 두 번 돌려라.
- parser backend, routing mode, 모델, reasoning effort, timeout, retries가 같아야 비교 의미가 있다.
- 실패 케이스도 버리지 말고 `final_error_message` 차이를 같이 봐라.
