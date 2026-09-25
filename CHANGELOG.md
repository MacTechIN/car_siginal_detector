# CHANGELOG — 버전 기록

각 버전은 git 태그(`v0.1`, `v0.2`, `v0.3`)로 표시되어 있습니다. 작업 과정의 자세한 내용은 [history.md](history.md)를 참고하세요.

## v0.4 — 2026-09-25 · 네이티브 Windows 앱

- **`windows_app/`: C# WPF(.NET 8) 데스크톱 앱 `CarSignalDetector.exe`** (단일 파일, 약 200KB).
  - 시작·정지(F5/Esc)와 옵션(음성 안내 · 녹화 · 음성 라벨링)을 제공합니다.
  - 화면에 음성 자막, 실시간 영상, 상황판(신호등·위험·앞차·차선·분석 결과·음성 라벨), 이벤트 로그, 엔진 로그를 보여줍니다.
  - 출발 전 점검, 학습, 녹화 폴더 버튼이 있습니다.
  - 명령줄 옵션 `--autostart`, `--snapshot`, `--exit-after`를 지원합니다.
- 엔진 서버 모드 `python -m csd --serve PORT` (`csd/server.py`): 상태 JSON, 주석 영상, 정상 종료를 제공하며 127.0.0.1에서만 열립니다.
- `build.bat`, `make_shortcut.bat`(바탕화면 바로가기).
- 테스트 85개.

## v0.3 — 2026-09-25 · USB 케이블 영상 전송

커밋 `d04702b` (이전 커밋 `80f646e`, `a0702dc` 포함)

- **카메라 영상을 USB 케이블로 전송합니다.** 노트북 Wi-Fi는 인터넷용으로 그대로 씁니다.
  - 펌웨어 `firmware/camera_web_server/usb_stream.cpp`: 보드의 네이티브 USB 포트로 JPEG·상태 패킷을 보내고, 설정 명령을 받습니다.
  - PC `csd/usb_camera.py`: 패킷 파서(잘린 패킷 뒤에도 재동기화), DTR/RTS를 끈 상태로 포트 열기, 자동 재연결, 카메라 찾기 재시도.
  - 실측: XGA **27.7fps**(Wi-Fi는 9.2fps), UXGA 11.3fps. 전송 상한은 약 650KB/s.
- 앱, 녹화 도구, 출발 전 점검이 USB를 먼저 찾고, 없으면 Wi-Fi로 넘어갑니다(`camera.transport: auto`).
- 출발 전 점검에 인터넷 연결과 경로 확인을 추가했습니다.
- `DEMO.md`에 인쇄용 한 장 요약을 넣었습니다(USB 케이블 하나로 연결, 예비는 `CSD-CAM` Wi-Fi).
- 테스트 83개.

## v0.2 — 2026-09-24 · 녹화, 음성 라벨링, 학습, 차량 시연 도구

커밋 `64ccb70`

- **녹화와 재생:**
  - `tools/record.py`, `python -m csd --record`: 원본 JPEG와 도착 시각을 저장합니다.
  - `--source`로 기록 시각대로 재생하고, `--realtime`을 주면 늦은 프레임을 건너뜁니다.
- **카메라 연결:**
  - 펌웨어가 알려진 Wi-Fi에 연결하지 못하면 자체 AP `CSD-CAM`을 엽니다. mDNS 이름은 `esp32cam`입니다.
  - PC가 카메라를 자동으로 찾습니다(`csd/discover.py`).
  - 공식 예제의 `camera_config_t` 초기화 누락 결함을 고쳤습니다.
- **음성 라벨링**(`--label-voice`):
  - 오프라인 한국어 음성 인식 Vosk를 씁니다. 방해 단어와 "문장 전체가 정확히 일치" 규칙으로 오인식을 막습니다.
  - 합성 음성 평가: 라벨 21/21 정확, 일반 대화 오인 0/15.
  - 안내 음성이 나오는 동안에는 마이크 입력을 무시합니다. "취소"와 "맞아"를 지원합니다.
- **신호등 분류기 학습:**
  - `tools/build_tl_dataset.py`: 라벨 이벤트 단위로 train/val을 나눕니다.
  - `tools/train_tl.py`: YOLO26n-cls로 학습해 `models/tl_cls.pt`를 만들고, 앱이 자동으로 사용합니다.
- **시연 도구:** `tools/preflight.py`, `demo.bat`, `train.bat`, 전체 화면 모니터, `DEMO.md`.
- 카메라 펌웨어 소스를 `firmware/`에 넣었습니다(Wi-Fi 비밀번호 파일 제외).
- 테스트 80개.

## v0.1 — 2026-09-24 · MVP

커밋 `741c72f`

- ESP32-S3 + OV3660 영상을 PC에서 분석합니다. 로그 형식은 `id : [이름] : "상태"`입니다.
- 검출·추적: YOLO26s + ByteTrack.
- 기능:
  - 한국 신호등 3·4구 상태와 점멸
  - 브레이크등, 방향지시등, 비상등
  - 한국 번호판
  - 차선과 차선 이탈
  - 자차 정지/주행
  - 충돌 위험(TTC), 앞차 상태, 끼어들기
  - 주변 상황
- 한국어 음성 안내(SAPI Heami, 우선순위 큐).
- 운전 상황 모니터(음성 자막, 상황판, 이동 경로, 이벤트 로그).
- 문서: `app_spec.md`, `research.md`(오케스트레이션 조사), `architech.md`, `dev_plan.md`, `HANDOVER.md`.
- 테스트 48개.
