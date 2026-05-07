# Backend

Scholium selected-region MVP 백엔드 runbook이다.

## 설치

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 실행

루트의 `.env` 파일이 준비되어 있어야 한다.

```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

참고: `data/rendered_pages`는 앱 startup 시 자동 생성된다. `image_url`은 absolute URL로 반환하지만, 배포/프록시 환경에서는 base URL 처리 재검토가 필요하다.

## 확인

- 브라우저 또는 HTTP 클라이언트로 `GET /health`
- 예: `curl http://127.0.0.1:8000/health`

## PDF 업로드 확인

```bash
curl -F "file=@../data/raw_pdfs/W1.Lecture01-Financial Management and Firm Value.pdf" \
  http://127.0.0.1:8000/api/documents
```

업로드가 성공하면 background로 아래 순서가 자동 시작된다.

- render
- parse / page_manifest precondition
- pass1
- document synthesis

기본 MVP에서는 여기서 viewer가 selected-region `on_demand` 모드로 준비된다. `pass2`는 precomputed anchor-click legacy/debug artifact가 필요할 때만 `SCHOLIUM_PRECOMPUTE_ANCHORED_EXPLANATIONS=true`로 켠다.

## Processing 상태 확인

```bash
curl http://127.0.0.1:8000/api/documents/doc_xxx/processing
watch -n 2 "curl -s http://127.0.0.1:8000/api/documents/doc_xxx/processing"
```

processing 응답에는 coarse status, `stage/current_stage(render/pass1/synthesis/pass2)`, page-level 진행 카운트, `pass1_failed_pages`, `pass1_processed_pages`, `current_page_number`, `recent_failures`가 같이 들어간다. 현재 기본 viewer readiness는 `render_ready_for_viewer`, `page_context_ready_pages`, `document_context_ready`를 함께 봐야 한다.

## 자동 생성 artifact 확인

완료 후 아래 파일들이 자동 생성되는지 보면 된다.

```bash
find ../data/rendered_pages/doc_xxx -maxdepth 1 -type f | sort | head
find ../data/analysis/doc_xxx -maxdepth 3 -type f | sort
find ../data/parsed/doc_xxx -maxdepth 2 -type f | sort
```

## Canonical Parse Artifact 확인

기본 parser backend는 `DOCUMENT_PARSER_BACKEND`로 선택할 수 있다.

- `pymupdf4llm`: 실제 parser adapter 사용
- `stub`: fallback / smoke test용

pass1 routing mode도 env flag로 제어할 수 있다.

- `PASS1_ROUTING_MODE=hybrid`: parse/page_manifest 기반 text-first + selective multimodal
- `PASS1_ROUTING_MODE=legacy`: 기존 full-page multimodal pass1 rollback

작은 PDF로 canonical parse artifact를 만들려면:

```bash
cd backend
source .venv/bin/activate
DOCUMENT_PARSER_BACKEND=pymupdf4llm .venv/bin/python - <<'PY'
from pathlib import Path
from app.services.document_parser import get_default_document_parser
from app.services.storage import StorageService

document_id = "doc_parse_smoke"
pdf_path = Path("../data/raw_pdfs/W1.Lecture01-Financial Management and Firm Value.pdf")
parser = get_default_document_parser()
artifact = parser.parse_document(document_id, pdf_path)
storage = StorageService()
print(storage.save_parse_artifact(document_id, artifact.model_dump(mode="json")))
PY
```

생성 경로:

```bash
../data/parsed/doc_parse_smoke/document_parse.json
```

page mirror는 기본 강제 저장이 아니라 lazy materialization이다.

업로드 파이프라인에 parse integration이 붙어 있으면, 처리 중 아래 파일도 같이 확인하면 된다.

```bash
find ../data/parsed/doc_xxx -maxdepth 2 -type f | sort
cat ../data/parsed/doc_xxx/document_parse.json | head -n 40
cat ../data/parsed/doc_xxx/page_manifest.json | head -n 80
```

pass1이 실제로 어떤 경로를 탔는지도 확인할 수 있다.

```bash
python3 - <<'PY'
import json
from pathlib import Path

base = Path("../data/analysis/doc_xxx/pages")
counts = {}
for path in sorted(base.glob("*/page_analysis_pass1.json")):
    payload = json.loads(path.read_text())
    page_number = payload["result"]["page_number"]
    pass1_path = payload["meta"].get("pass1_path", "unknown")
    counts[pass1_path] = counts.get(pass1_path, 0) + 1
    print(page_number, pass1_path, payload["meta"].get("route_label"))
print(counts)
PY
```

pass1 routing까지 확인하려면:

```bash
python3 - <<'PY'
import json
from pathlib import Path

document_id = "doc_xxx"
base = Path(f"../data/analysis/{document_id}/pages")
for path in sorted(base.glob("*/page_analysis_pass1.json")):
    payload = json.loads(path.read_text(encoding="utf-8"))
    meta = payload["meta"]
    print(
        path.parent.name,
        meta.get("pass1_path"),
        meta.get("route_label"),
        len(payload["result"]["candidate_anchors"]),
    )
PY
```

문서 단위로 text-first / multimodal / escalated 분포를 보려면:

```bash
python3 - <<'PY'
import json
from pathlib import Path

document_id = "doc_xxx"
base = Path(f"../data/analysis/{document_id}/pages")
summary = {"text-first": [], "multimodal": [], "escalated": [], "missing": []}
for page_number in range(1, 1000):
    path = base / str(page_number) / "page_analysis_pass1.json"
    if not path.exists():
        if page_number > 1 and not (base / str(page_number - 1)).exists():
            break
        continue
    payload = json.loads(path.read_text(encoding="utf-8"))
    summary.setdefault(payload["meta"].get("pass1_path") or "unknown", []).append(page_number)
print(summary)
PY
```

processing benchmark를 보려면:

```bash
cat ../data/analysis/doc_xxx/processing_benchmark.json
```

corpus benchmark를 모으려면:

```bash
./.venv/bin/python scripts/run_benchmark_corpus.py \
  --pdf-dir ../data/raw_pdfs \
  --limit 5
```

특정 PDF 리스트만 돌리려면:

```bash
./.venv/bin/python scripts/run_benchmark_corpus.py \
  /tmp/a.pdf \
  /tmp/b.pdf \
  --output /tmp/corpus_run.json
```

오래 걸리는 문서를 timeout으로 넘기려면:

```bash
./.venv/bin/python scripts/run_benchmark_corpus.py \
  --pdf-dir ../data/raw_pdfs \
  --limit 5 \
  --per-doc-timeout-seconds 900
```

기본 corpus output 위치는 `../docs/perf_runs/<timestamp>.json`이다. 이 디렉터리는 generated artifact 용도라 기본적으로 git에 커밋하지 않는다. 공유할 결론은 `docs/perf/`의 concise decision/plan 문서로 옮긴다.

짧게 핵심 수치만 보려면:

```bash
python3 - <<'PY'
import json
from pathlib import Path

document_id = "doc_xxx"
payload = json.loads((Path(f"../data/analysis/{document_id}/processing_benchmark.json")).read_text())
print(payload["final_status"], payload["final_error_message"])
print("total_seconds =", payload["total_processing_time_seconds"])
print("pass1 paths =", {
    "text-first": payload["pass1_text_first_pages"],
    "multimodal": payload["pass1_multimodal_pages"],
    "escalated": payload["pass1_escalated_pages"],
})
print("openai_calls =", {
    "total": payload["openai_call_count_total"],
    "pass1": payload["openai_pass1_call_count"],
    "synthesis": payload["openai_synthesis_call_count"],
    "pass2": payload["openai_pass2_call_count"],
})
PY
```

corpus run 결과를 짧게 보려면:

```bash
python3 - <<'PY'
import json
from pathlib import Path

path = sorted(Path("../docs/perf_runs").glob("*.json"))[-1]
payload = json.loads(path.read_text())
print("run =", path.name)
print("run_id =", payload["run_id"])
print("git_head =", payload["git_head"])
print("summary =", payload["corpus_summary"])
for document in payload["documents"][:5]:
    print(
        document["filename"],
        document["collection_status"],
        document["final_status"],
        document["openai_call_count_total"],
    )
PY
```

## SQLite 확인

```bash
sqlite3 ../data/scholium_dev.sqlite3 ".tables"
sqlite3 ../data/scholium_dev.sqlite3 "select document_id, filename, status, total_pages, error_message from documents;"
sqlite3 ../data/scholium_dev.sqlite3 "select page_number, image_path, render_status, width, height from pages where document_id = 'doc_xxx' order by page_number limit 5;"
sqlite3 ../data/scholium_dev.sqlite3 "select page_number, render_status, pass1_status, pass1_error_message, pass2_status, pass2_error_message from pages where document_id = 'doc_xxx' order by page_number;"
```

## Public Read API 확인

```bash
curl http://127.0.0.1:8000/api/documents/doc_xxx
curl http://127.0.0.1:8000/api/documents/doc_xxx/processing
curl http://127.0.0.1:8000/api/documents/doc_xxx/summary
curl http://127.0.0.1:8000/api/documents/doc_xxx/pages/1
curl -o /tmp/page1.png "$(curl -s http://127.0.0.1:8000/api/documents/doc_xxx/pages/1 | jq -r .image_url)"
```

## Selected-Region 설명 API 확인

page context가 준비된 페이지에서 normalized bbox를 보내면, backend가 compact `SelectionContext`를 만들고 on-demand selected-region explanation을 생성한다.

```bash
curl -X POST http://127.0.0.1:8000/api/documents/doc_xxx/pages/1/selection-explanation \
  -H "Content-Type: application/json" \
  -d '{"selected_bbox":[0.12,0.18,0.32,0.08]}'
```

성공하면 `selection_id`, `selected_bbox`, `concept_title`, `short_explanation`, `long_explanation`, `source_cues`가 포함된 JSON이 돌아온다. 프런트엔드는 이 결과를 floating academic annotation panel로 보여준다.

## Interaction Log 확인

```bash
curl -X POST http://127.0.0.1:8000/api/logs \
  -H "Content-Type: application/json" \
  -d '{"document_id":"doc_xxx","page_number":1,"anchor_id":null,"event_type":"page_view"}'

curl -X POST http://127.0.0.1:8000/api/logs \
  -H "Content-Type: application/json" \
  -d '{"document_id":"doc_xxx","page_number":1,"anchor_id":"selection_abc","event_type":"selection_explanation_success"}'

sqlite3 ../data/scholium_dev.sqlite3 \
  "select event_id, document_id, page_number, anchor_id, event_type, timestamp from interaction_logs order by timestamp desc limit 20;"
```

`anchor_click`와 `related_page_jump` 이벤트는 legacy/debug precomputed anchor-click viewer용으로 남아 있다.
