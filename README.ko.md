# receipts

에이전트가 쓴 숫자마다 영수증을 붙이고, 영수증이 없는 숫자는 표시한다.

> **상태: 배포 전입니다.** 대조 스크립트, 에이전트 스킬, 미국 공시(SEC EDGAR) 연결, 평가 도구가 저장소에 있고, GitHub에서 플러그인으로 설치됩니다. 배포용 평가 숫자는 아직 없으니 미리보기로 봐 주세요.

[English](README.md)

## 문제

에이전트가 보고서를 쓰고 숫자를 확인했다고 말합니다. 실제로는 확인하지 않았거나 일부만 확인한 경우가 많고, 읽는 사람은 어느 숫자가 확인된 것인지 알 수 없습니다.

## 방식

모델은 계획만 쓰고, 판정은 코드가 합니다.

1. 코드가 문서의 숫자를 전부 찾습니다. 이 개수가 분모입니다.
2. 모델이 숫자마다 문장, 출처 칸, 계산식을 적습니다. 값은 적지 않습니다.
3. 코드가 출처를 읽고 다시 계산해 `verified`(확인됨), `wrong`(틀림), `unverifiable`(확인 불가)로 판정합니다.
4. 계획에서 빠진 숫자는 `uncovered`(미대조)로 나옵니다.

## Claude Code에 스킬 설치하기

```
/plugin install receipts --marketplace sinkytiger/receipts
```

한 줄 설치는 Claude Code 2.1.275 이상에서 됩니다. 그보다 낮은 버전에서는 두 단계로 합니다.

```
/plugin marketplace add sinkytiger/receipts
/plugin install receipts@receipts
```

설치한 뒤 "receipts로 report.md의 숫자를 sales.csv와 대조해 줘"처럼 요청합니다. 스킬 이름을 말하지 않았을 때 얼마나 잘 불러오는지는 아직 재지 않았습니다. 스킬은 세션마다 약 160토큰을, 실행할 때 약 3,000토큰을 씁니다(`claude plugin details`의 추정값).

두 방식 모두 윈도우의 Claude Code 2.1.326에서 명령줄(`claude plugin install ...`)로, 설정이 없는 상태에서 설치해 확인했습니다.

## 에이전트 없이 스크립트만 써 보기

파이썬만 있으면 됩니다. 파이썬 3.9 이상을 기준으로 썼고, 지금까지는 3.12에서만 돌려 봤습니다.

```sh
python skills/receipts/scripts/receipts.py check examples/quickstart/claims.json
```

예제 보고서는 동부 매출이 12.4% 늘었다고 쓰지만, 출처로 다시 계산하면 11.9%입니다. 결과 첫 줄은 이렇게 나옵니다.

```
9 numbers / 6 verified / 1 wrong / 1 unverifiable / 1 uncovered   (skipped: 1 date, 2 heading_number, 1 label, 3 year)
```

지금 읽을 수 있는 출처는 CSV, TSV, JSON, 마크다운 표, 일반 텍스트, 엑셀 값, 그리고 미국 상장사가 SEC에 낸 공시 수치입니다.

## 미국 공시(SEC EDGAR)와 대조하기

회사가 10-K, 10-Q에 보고한 수치를 출처로 쓸 수 있습니다. 스크립트가 SEC의 EDGAR API에서 읽습니다.

```json
{"edgar": "AAPL", "concept": "us-gaap:Assets", "unit": "USD", "period": "2026-09-26"}
```

- SEC는 자동 접속 프로그램에 이름과 이메일을 밝히라고 요구합니다. 환경 변수 `RECEIPTS_SEC_USER_AGENT`에 본인 이름과 이메일을 넣어야 합니다. 기본값은 없고, 비어 있으면 요청을 보내지 않고 멈춥니다. 이 문자열은 SEC로 보내는 요청에만 들어가고 어디에도 저장하지 않습니다.
- 회사마다 한 번만 내려받아 `.receipts/edgar/`에 저장합니다. 영수증에 내려받은 날짜가 나옵니다. 요청은 1초에 5번 이하로 보냅니다(SEC 허용치는 10번).
- 기간은 날짜가 정확히 맞아야 합니다. 분기 수치와 누적 수치는 끝나는 날이 같고 시작일만 다르므로 서로 바뀌어 통과하지 않습니다.
- 같은 기간의 값이 공시마다 다르면(정정·재분류) 판정을 미루고 값들을 보여 줍니다. 계획에서 공시를 지정하거나 최초·최신 보고를 고르면 그 값으로 대조합니다.
- `receipts.py edgar concepts AAPL --match revenue`, `receipts.py edgar facts AAPL us-gaap:Assets`로 회사가 쓰는 항목 이름과 기간 날짜를 찾습니다.
- 부문별·제품별·지역별 수치는 이 API에 없습니다. 회사가 직접 정의한 항목도, 살펴본 5개 회사에서는 들어 있지 않았습니다.

[examples/edgar](examples/edgar)는 애플의 2026 회계연도를 다룬 짧은 글이고, 숫자 하나를 일부러 틀리게 썼습니다. SEC가 2026-11-20에 돌려준 자료를 잘라 낸 사본이 들어 있어 네트워크 없이 돌아갑니다.

```sh
python skills/receipts/scripts/receipts.py check examples/edgar/claims.json --edgar-cache examples/edgar/snapshot
```

```
7 numbers / 6 verified / 1 wrong / 0 unverifiable / 0 uncovered   (skipped: 1 date, 1 identifier, 3 year)
```

글은 연구개발비가 매출의 10.6%라고 쓰지만, 공시된 두 수치로 다시 계산하면 9.6%입니다.

## 평가 진행 상황

- **E1 (정답 계획을 주고 스크립트만 평가):** 개발용 문제(문서 64개, 문장 960개, 물러남 사례 96개)에서 통과 기준 8개를 모두 충족했습니다. 실행 기록은 [bench/runs.jsonl](bench/runs.jsonl)에 있습니다. 개발용 숫자이고, 대표 숫자는 배포 커밋에서 한 번만 돌리는 최종용 문제에서 나옵니다. 최종용은 아직 돌리지 않았습니다.
- **E2 (모델이 계획을 쓰는 전체 흐름):** 돌리지 않았습니다. E1은 계획이 맞을 때 스크립트가 맞게 읽고 비교하는지만 보여 줍니다.
- **E3 (미국 공시 연결):** 평가 기준은 연결 코드를 쓰기 전에 등록했습니다. 연결이 돌려준 값을 공시 문서에 인쇄된 숫자와 비교합니다. 개발용 5개 회사(공시 9건, 뽑은 수치 266개)에서 통과 기준 6개를 모두 충족했습니다.
  - 틀림으로 판정된 수치는 없었습니다.
  - 공시를 지정했을 때 266개 중 266개가 확인됐습니다.
  - 공시를 지정하지 않았을 때 262개가 확인됐고, 4개는 공시마다 값이 달라 판정을 미뤘습니다.
  - 아무것도 찾지 못해야 하는 위치 지정 180건은 모두 확인 불가로 나왔고, 연락처가 없거나 저장된 파일이 깨졌거나 서버에 닿지 않는 실행 15건은 모두 멈췄습니다.

  실행 기록은 [bench/edgar/runs.jsonl](bench/edgar/runs.jsonl)에 있습니다. 개발용 숫자이고, 최종용 20개 회사는 배포 커밋에서 한 번만 돌리며 아직 내려받지 않았습니다. 알아 둘 점이 두 가지 있습니다. 여섯 번째 기준은 첫 실행 뒤에 추가했습니다. 일부러 고장 낸 도구 하나가 처음 다섯 기준을 통과했기 때문이고, 경위는 [평가 계획의 변경 기록](docs/eval-plan.md)에 있습니다. 또 개발용 회사 중 XOM은 새 법인으로 공시하고 있어 아직 10-K가 없으므로 공시가 10건이 아니라 9건입니다.

E1 문서는 문장 틀로 만든 합성 문서이고 영어와 한국어가 절반씩입니다. E3 시험 문장은 실제 공시의 숫자로 만듭니다.

## 문서

- [skills/receipts/references/plan-format.md](skills/receipts/references/plan-format.md): 계획 양식과 규칙 (영어)
- [docs/design.md](docs/design.md): 동작 방식과 하지 않는 것 (영어)
- [docs/eval-plan.md](docs/eval-plan.md): 코드를 쓰기 전에 정한 평가 기준 (영어)

## 라이선스

MIT
