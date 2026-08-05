# 인코딩 규칙

- 코드 및 문서 파일을 생성하거나 수정한 뒤 최종 저장 인코딩은 반드시 UTF-8로 적용한다.
- 한글이 포함된 파일은 저장 후 UTF-8로 다시 읽어 인코딩 오류 및 문자 깨짐 여부를 확인한다.
- 웹 정적 파일(HTML, CSS, JavaScript)은 HTTP 응답에도 `charset=utf-8`을 명시한다.
- 기존 파일의 인코딩을 변경해야 할 때도 내용 손실 없이 UTF-8로 변환한다.
# SimHash 모듈화 규칙

- SimHash 기능을 수정하거나 확장할 때 새로운 실행 파일·래퍼 파일을 생성하지 않는다.
- SimHash 관련 기능은 반드시 `simhash_matcher/public_simhash.py` 기존 파일에만 작성하고 모듈화한다.
- URL 파싱, 제목·본문·metadata 추출, SimHash 생성, DB hash 비교, 외부 HTTP 엔드포인트는 `public_simhash.py` 단일 파일만으로 처리 가능해야 한다.
- 기능 변경 후 `public_simhash.py`만 전달해도 SimHash 기능의 핵심 동작을 이해·적용할 수 있도록 README와 호출 방법을 함께 갱신한다.
