# SimHash 정확 중복 확인 모듈

크롤러에서 사용할 경량 SimHash 모듈입니다. 제목과 본문으로 64비트 SimHash를 생성하고, DB의 지정 컬럼에 **완전히 같은 SimHash 값이 있는지**만 `True`/`False`로 반환합니다.

## 구성

```text
simhash_matcher/
├── __init__.py
├── simhash_matcher.py  # 모듈 구현
└── README.md           # 사용 문서
```

프로젝트 루트의 `requirements.txt`에는 `simhash==2.1.2`가 정의되어 있습니다.

## 설치

프로젝트 루트에서 가상환경을 활성화한 뒤 설치합니다.

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## 제공 함수

### `make_simhash(title, content) -> int`

정규화된 `제목 + "\n---CONTENT---\n" + 본문`을 64비트 SimHash 정수값으로 변환합니다.

- 정규화: 유니코드 NFC, 연속 공백 통합, 앞뒤 공백 제거
- 구현: PyPI [`simhash`](https://pypi.org/project/simhash/) 라이브러리의 `Simhash(text, f=64)`
- 반환 예: 정수 `3437162089187550772`

### `format_simhash(simhash) -> str`

DB 저장 및 비교를 위한 16자리 소문자 16진수 문자열로 변환합니다.

```python
format_simhash(simhash)  # 예: "2fb34c0aa6310e34"
```

### `has_simhash_match(connection, simhash, *, table, column="simhash") -> bool`

지정 테이블·컬럼에서 정확히 일치하는 SimHash가 존재하는지 확인합니다.

- `True`: 이미 저장된 동일 SimHash 존재
- `False`: 일치값 없음
- SQL: `SELECT 1 FROM {table} WHERE {column} = %s LIMIT 1`
- 해밍 거리나 유사도는 계산하지 않습니다.

## 크롤러 적용 예시

```python
from simhash_matcher.simhash_matcher import (
    format_simhash,
    has_simhash_match,
    make_simhash,
)

simhash_value = make_simhash(title, content)

if has_simhash_match(
    connection,
    simhash_value,
    table="crawled_pages",
    column="simhash",
):
    # 호출자에게는 중복 여부만 반환
    return {"duplicate": True}

# 신규 저장 시에는 동일한 형식으로 기록
simhash_for_storage = format_simhash(simhash_value)
return {"duplicate": False}
```

## DB 저장 규칙

- 권장 컬럼명: `simhash`
- 권장 타입: `CHAR(16)` 또는 `VARCHAR(16)`
- 저장값: `format_simhash()`의 결과
- `table`, `column`에는 사용자 입력값이 아닌 코드 상수만 전달합니다.

## 범위와 주의사항

- 이 모듈은 **동일 SimHash의 정확 일치**만 판단합니다.
- URL·제목·본문 원문 저장이나 DB insert는 크롤러/저장 레이어의 책임입니다.
- SimHash의 해밍 거리 기반 유사 판정이 필요하면 별도 함수와 운영 임계값 정책을 추가해야 합니다.
- DB 드라이버가 `%s` placeholder를 지원하지 않으면 `has_simhash_match()` 쿼리를 해당 드라이버 문법으로 조정해야 합니다.

## 검증

프로젝트 루트에서 실행합니다.

```powershell
.\.venv\Scripts\python.exe -m unittest -v test_simhash_matcher.py
```
