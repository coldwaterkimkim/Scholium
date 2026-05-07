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

## Corpus Baseline Runner

문서 1개가 아니라 PDF 묶음 기준 baseline을 모으려면 corpus runner를 쓴다.

- 실행 모드는 현재 `sequential` 고정이다.
- 각 PDF는 fresh document로 다시 처리한다.
- 출력은 기본값으로 `docs/perf_runs/<timestamp>.json`에 저장된다.

디렉터리 기준 실행:

```bash
cd backend
PIPELINE_MODE=hybrid PASS2_EXECUTION_MODE=all_pages \
./.venv/bin/python scripts/run_benchmark_corpus.py \
  --pdf-dir ../data/raw_pdfs \
  --limit 5 \
  --mode-name baseline_hybrid_all_pages
```

PDF 리스트 기준 실행:

```bash
cd backend
./.venv/bin/python scripts/run_benchmark_corpus.py \
  /tmp/a.pdf \
  /tmp/b.pdf \
  /tmp/c.pdf
```

너무 오래 걸리는 문서를 다음 문서로 넘기려면:

```bash
cd backend
PIPELINE_MODE=v2_spine V2_SPINE_MODE=active PASS2_EXECUTION_MODE=hard_pages_only \
./.venv/bin/python scripts/run_benchmark_corpus.py \
  --pdf-dir ../data/raw_pdfs \
  --limit 5 \
  --per-doc-timeout-seconds 900 \
  --mode-name v2_spine_active_hard_pages_only \
  --output-dir ../docs/perf_runs
```

corpus run JSON에는 아래가 같이 들어간다.

- top-level 재현성 메타:
  - `run_id`
  - `git_head`
  - `runner_version`
  - `mode_name`
- per-document 상태:
  - `collection_status`: `completed | failed | benchmark_missing | interrupted`
  - `benchmark_available`
  - `collection_error`
  - `source_pdf_relpath`
- corpus summary:
  - `document_count`
  - `completed_count`
  - `failed_count`
  - `usable_benchmark_count`
  - `benchmark_missing_count`
  - `interrupted_count`
  - `success_rate`
  - `metrics.<field>.mean|median|min|max`

짧게 확인:

```bash
python3 - <<'PY'
import json
from pathlib import Path

path = sorted(Path("docs/perf_runs").glob("*.json"))[-1]
payload = json.loads(path.read_text())
print(path)
print(payload["run_id"], payload["git_head"], payload["runner_version"])
print(payload["corpus_summary"])
for document in payload["documents"][:3]:
    print(document["filename"], document["collection_status"], document["final_status"])
PY
```

전/후 비교 원칙:

- 같은 corpus 입력을 써라.
- `run_config`와 `git_head`가 다르면 같은 baseline으로 비교하지 마라.
- `success_rate`와 `interrupted_count`를 같이 봐야 최적화가 성공률을 해치지 않았는지 판단할 수 있다.
- `run_config.source_root`는 resolved absolute path로 저장된다.
- `source_pdf_relpath`는 run 내부 primary comparison key다.
- serious comparison은 같은 corpus layout과 같은 `--pdf-dir` 기준 run을 권장한다.

## Mode Comparison

corpus run JSON 2개 이상을 비교하려면:

```bash
cd backend
./.venv/bin/python scripts/compare_pipeline_modes.py \
  ../docs/perf_runs/baseline.json \
  ../docs/perf_runs/active.json
```

출력:

- `docs/perf_runs/<timestamp>_comparison.json`
- `docs/perf_runs/<timestamp>_comparison.md`

비교 규칙:

- join key는 `document_id`가 아니라 `source_pdf_relpath`다.
- `source_root`가 run 간 다르면 warning이 남는다.
- 오래된 run에 metric이 없으면 comparison layer에서는 `0`이 아니라 `null`로 다룬다.
- Markdown에는 `missing in run`으로 표시된다.

## Compat QA Sample Export

기존 artifact만 읽어서 compat / llm page QA 샘플을 뽑으려면:

```bash
cd backend
./.venv/bin/python scripts/export_pass2_qa_samples.py \
  doc_7e2cd91b41274471af9a533e8c87b4a9 \
  --limit-pages 3
```

또는 analysis dir 스캔:

```bash
cd backend
./.venv/bin/python scripts/export_pass2_qa_samples.py \
  --analysis-dir ../data/analysis \
  --limit-docs 2 \
  --limit-pages 4
```

출력:

- `docs/perf_runs/<timestamp>_qa_samples.json`
- `docs/perf_runs/<timestamp>_qa_samples.md`

QA sample에는 아래가 들어간다.

- `source_pdf_relpath`
- `page_image_relpath`
- `pass2_generation_mode`
- `pass1_path`
- `route_label`
- `hard_page_score`
- `recommended_execution`
- anchor labels / types
- short / long explanation
- `page_role`
- `page_summary`
