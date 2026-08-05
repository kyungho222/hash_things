# Public SimHash

## 1. 목적

크롤링 서비스가 URL, DB 이름, 챗봇 ID를 전달하면 이 모듈이 페이지를 파싱하고 SimHash를 생성한 뒤 해당 챗봇의 학습 테이블에서 exact 중복을 검사합니다.

~~~text
크롤링 서비스
  → URL + dbname + chatbotid 전송
  → Playwright 페이지 접근·제목/본문/metadata 파싱
  → 128비트 SimHash 생성
  → 해당 DB·챗봇 LEARN_LIST의 hash exact 비교
  → duplicate/save/skipped 응답
~~~

모든 기능은 public_simhash.py 한 파일에 있습니다.

## 2. 실행

프로젝트 루트에서 실행합니다.

~~~bash
python -m pip install -r requirements.txt
python -m playwright install chromium
uvicorn simhash_matcher.public_simhash:app --host 0.0.0.0 --port 8000
~~~

## 3. DB 연결 설정

서비스 환경에는 MariaDB 연결 정보만 설정합니다. DB 이름과 학습 테이블은 요청 payload로 결정되므로 SIMHASH_DB_NAME, SIMHASH_DB_TABLE은 사용하지 않습니다.

~~~env
SIMHASH_DB_HOST=database-host
SIMHASH_DB_PORT=3306
SIMHASH_DB_USER=database-user
SIMHASH_DB_PASSWORD=database-password
~~~

테이블 이름은 chatbotid의 하이픈을 제거한 UUID 뒤 12자리로 결정합니다.

~~~text
chatbotid: ad1d9dbf-5165-4ad7-af7d-bf1850e784ee
table:     ASADAL_bf1850e784ee_LEARN_LIST
~~~

비교 컬럼은 hash이며, API는 테이블 또는 컬럼을 생성·변경하지 않습니다.

~~~sql
SELECT 1
FROM ASADAL_bf1850e784ee_LEARN_LIST
WHERE hash = %s
LIMIT 1;
~~~

현재 해밍 거리 비교 없이 exact 비교만 적용합니다.

## 4. 호출 방법

~~~http
POST /public_simhash
Content-Type: application/json
~~~

~~~json
{
  "url": "https://example.com/post/123",
  "dbname": "newvit",
  "chatbotid": "ad1d9dbf-5165-4ad7-af7d-bf1850e784ee"
}
~~~

- url: 파싱·해싱할 상세 페이지 URL
- dbname: 비교할 MariaDB database 이름
- chatbotid: 대상 챗봇 UUID. 해당 값으로 LEARN_LIST 테이블을 결정합니다.

~~~bash
curl -X POST "http://server-address:8000/public_simhash" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com/post/123","dbname":"newvit","chatbotid":"ad1d9dbf-5165-4ad7-af7d-bf1850e784ee"}'
~~~

## 5. 응답 및 처리 기준

신규 데이터:

~~~json
{
  "url": "https://example.com/post/123",
  "simhash": "749cd4acc8f40033822aed173ac4449f",
  "duplicate": false,
  "save": true,
  "skipped": false
}
~~~

크롤링 서비스는 원본 데이터와 simhash를 저장합니다.

중복 데이터:

~~~json
{
  "url": "https://example.com/post/123",
  "simhash": "749cd4acc8f40033822aed173ac4449f",
  "duplicate": true,
  "save": false,
  "skipped": false
}
~~~

크롤링 서비스는 저장하지 않습니다.

파싱·DB 비교 실패:

~~~json
{
  "url": "https://example.com/post/123",
  "simhash": "749cd4acc8f40033822aed173ac4449f",
  "duplicate": false,
  "save": false,
  "skipped": true,
  "skip_reason": "database_check_failed: OperationalError"
}
~~~

skipped는 신규 판정이 아니라 판정 실패입니다. 호출 서비스의 재시도 또는 별도 보관 정책을 적용해야 합니다.

## 6. 동시 요청 처리

- 전역 요청 진행 플래그는 사용하지 않습니다.
- 같은 URL·dbname·chatbotid 조합이 처리 중이면 기존 in-flight task를 재사용하고 결과를 기다립니다.
- DB 또는 챗봇이 다른 동일 URL은 독립 처리합니다.
- 서로 다른 URL도 독립 처리합니다.
- Playwright 렌더링은 고정값 10개까지 동시에 실행합니다.
- task 완료 후 in-flight 목록에서 제거됩니다.

GET /health는 현재 inflight 수와 max_concurrency 값을 반환합니다.

## 7. 모듈 직접 사용

~~~python
from simhash_matcher.public_simhash import make_simhash

value = make_simhash(subject, content)
~~~

URL 접근·파싱·DB 중복 비교까지 필요하면 POST /public_simhash를 사용합니다.
