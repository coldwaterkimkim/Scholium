# Backend

Scholium MVP v0 Step 2 백엔드 최소 실행 뼈대다.

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

## PDF 렌더링 확인

업로드 응답으로 받은 `document_id`를 넣어서 실행하면 된다.

```bash
cd backend
source .venv/bin/activate
python -m app.workers.render_worker doc_xxx
```

렌더 결과 파일 확인:

```bash
find ../data/rendered_pages/doc_xxx -maxdepth 1 -type f | sort | head
```

## SQLite 확인

```bash
sqlite3 ../data/scholium_dev.sqlite3 ".tables"
sqlite3 ../data/scholium_dev.sqlite3 "select document_id, filename, status, total_pages, error_message from documents;"
sqlite3 ../data/scholium_dev.sqlite3 "select page_number, image_path, render_status, width, height from pages where document_id = 'doc_xxx' order by page_number limit 5;"
```

## Public Read API 확인

```bash
curl http://127.0.0.1:8000/api/documents/doc_xxx
curl http://127.0.0.1:8000/api/documents/doc_xxx/summary
curl http://127.0.0.1:8000/api/documents/doc_xxx/pages/1
curl -o /tmp/page1.png "$(curl -s http://127.0.0.1:8000/api/documents/doc_xxx/pages/1 | jq -r .image_url)"
```

## Interaction Log 확인

```bash
curl -X POST http://127.0.0.1:8000/api/logs \
  -H "Content-Type: application/json" \
  -d '{"document_id":"doc_xxx","page_number":1,"anchor_id":null,"event_type":"page_view"}'

curl -X POST http://127.0.0.1:8000/api/logs \
  -H "Content-Type: application/json" \
  -d '{"document_id":"doc_xxx","page_number":1,"anchor_id":"p1_a3","event_type":"anchor_click"}'

sqlite3 ../data/scholium_dev.sqlite3 \
  "select event_id, document_id, page_number, anchor_id, event_type, timestamp from interaction_logs order by timestamp desc limit 20;"
```
