# architech.md — 기술 스택과 구조

## 1. 전체 구성

```
 ┌────────────────────────────┐   Wi-Fi 2.4GHz (MJPEG over HTTP)   ┌────────────────────────────────────────────┐
 │ ESP32-S3 N16R8 + OV3660     │ ─────── :81/stream ──────────────▶ │ Windows PC (Python 3.14, CPU)               │
 │ firmware: CameraWebServer   │ ◀────── /control?var=..&val=.. ─── │  csd 패키지                                 │
 │ (esp32_cam 프로젝트)         │         /status                    │  검출·추적 → 상태 분석 → 로그 + 음성 + 모니터 │
 └────────────────────────────┘                                    └────────────────────────────────────────────┘
```

- 카메라는 촬영과 전송만 합니다. 모든 추론은 PC에서 합니다(근거: research.md 2장).
- 카메라 펌웨어와 빌드 방법은 형제 프로젝트 `../esp32_cam`에 있습니다.
  - 핀맵: ESP32S3_EYE
  - 한글 경로 우회 빌드

## 2. 기술 스택

| 계층 | 기술 | 버전 (검증) | 비고 |
|---|---|---|---|
| 카메라 HW | ESP32-S3 (QFN56 rev0.2), Flash 16MB, PSRAM 8MB OPI, **OV3660** | — | CH340 UART와 네이티브 USB 포트 2개 |
| 카메라 FW | Arduino-ESP32 **3.3.12** `CameraWebServer` 예제 | — | `C:\arduino-esp32` 전용 코어로 빌드 |
| 언어 | Python | **3.14** (venv `.venv`) | |
| 검출 | Ultralytics **YOLO26s** (COCO) | ultralytics 8.4.161, torch 2.14 CPU | AGPL-3.0 |
| 추적 | **ByteTrack** (Ultralytics 내장) + `lap` | | `configs/tracker.yaml`: 낮은 fps에 맞춰 튜닝 |
| 영상 처리 | OpenCV | 5.0 | 차선, 광류, HSV 판별 |
| 번호판 | sauce-hug/korean-license-plate-detector YOLO 3종 | HF `best.pt` | 백그라운드 스레드에서 실행 |
| 음성 | Windows **SAPI5** (pywin32 312), 음성 **Microsoft Heami** | | 오프라인 |
| 모니터 | OpenCV 창 + **Pillow**(맑은 고딕으로 한글 렌더링) | Pillow 12.3 | `--show`, `--fullscreen` |
| 음성 라벨링 | **Vosk** `vosk-model-small-ko-0.22` (오프라인 한국어, 문장 목록 모드) + sounddevice | vosk 0.3.x | 방해 단어 + 정확 일치 규칙 |
| 신호등 분류기 학습 | Ultralytics **YOLO26n-cls** 파인튜닝 (96×96 정사각 크롭) | | `tools/train_tl.py` → `models/tl_cls.pt` |
| 카메라 연결 (기본) | **USB 케이블**: 펌웨어 `usb_stream.cpp`가 네이티브 USB(USB-Serial/JTAG)로 `CSDF`(JPEG)/`CSDJ`(상태) 패킷 전송. PC `usb_camera.py`(pyserial, 머리표 재동기화 파서) | pyserial 3.5 | XGA 27.7fps, 상한 약 650KB/s |
| 카메라 연결 (예비) | 펌웨어: 알려진 Wi-Fi → 실패 시 AP `CSD-CAM`, mDNS `esp32cam` / PC: `discover.py` 자동 탐색 | | XGA 약 9fps |
| 선택 가속 | OpenVINO 2026.4, onnxruntime 1.30 | | 배터리 모드에서는 이득 없음 (research.md 6장) |
| 테스트 | pytest 9 | 48 tests | 합성 이미지·시계열 기반 |

## 3. 폴더 구조

```
car_siginal_detector/
├─ app_spec.md          앱 기능 명세서 (사용자 요구사항)
├─ research.md          인식 방법 조사
├─ architech.md         (이 문서) 기술 스택과 구조
├─ dev_plan.md          마이크로 단계 개발 계획과 진행 상태
├─ HANDOVER.md          인수인계서 (현재 상태, 실행법, 알려진 문제)
├─ history.md           작업 이력
├─ requirements.txt
├─ configs/
│  ├─ default.yaml      모든 설정 (카메라, 모델, 임계값, 음성)
│  └─ tracker.yaml      ByteTrack 설정
├─ csd/                 애플리케이션 패키지  (python -m csd)
│  ├─ app.py            파이프라인, 스케줄링, CLI
│  ├─ stream.py         MJPEG 수신 스레드 (최신 프레임만 유지) / 파일 재생
│  ├─ camera_ctl.py     /control 로 센서 설정 적용
│  ├─ detect.py         YOLO + ByteTrack → Detection(tid, name, box, conf)
│  ├─ traffic_light.py  한국 신호등 램프 판별 + 점멸 판정
│  ├─ vehicle_lights.py 브레이크 / 방향지시등 / 비상등
│  ├─ plate.py          번호판 검출 → 원근보정 → 글자 인식 → 투표
│  ├─ lanes.py          자차 차선 다각형, 이탈 판정
│  ├─ ego_motion.py     자차 정지/주행 (배경 광류)
│  ├─ risk.py           앞차 선택, TTC, 선행차 상태, 끼어들기
│  ├─ state.py          StateSmoother(시간 투표 + 히스테리시스), EventLog
│  ├─ messages.py       한국어 안내 문구와 우선순위
│  ├─ tts.py            SAPI 음성 우선순위 큐
│  ├─ overlay.py        영상 위 박스, 궤적, 차선, 진행 방향
│  ├─ dashboard.py      운전 상황 모니터 (자막, 상황판, 로그, 음성 라벨 상태)
│  ├─ usb_camera.py     USB 케이블 카메라 수신 (패킷 파서, 설정 명령, 자동 재연결)
│  ├─ discover.py       Wi-Fi 카메라 자동 탐색 (설정 주소 → AP → mDNS → 서브넷)
│  ├─ recorder.py       녹화 세션 (원본 JPEG + timestamps.csv + meta.json)
│  ├─ voice_label.py    마이크 → Vosk → 라벨 (자기 음성 차단)
│  ├─ labeling.py       라벨 시점 전후 신호등 크롭 저장, 취소/맞아 처리
│  └─ tl_classifier.py  학습된 신호등 분류기 (있으면 우선 사용)
├─ tests/               단위·통합 테스트 (카메라 없이 실행, 음성 테스트는 합성 음성 사용)
├─ tools/
│  ├─ fetch_models.py   모델 다운로드 (YOLO, 번호판, Vosk)
│  ├─ record.py         녹화 전용
│  ├─ preflight.py      출발 전 점검 (전원, 디스크, 모델, 음성, 마이크, 카메라 fps)
│  ├─ build_tl_dataset.py  크롭 → dataset/tl_cls/{train,val} (라벨 이벤트 단위 분할)
│  ├─ train_tl.py       분류기 학습 → models/tl_cls.pt
│  └─ eval_voice_labels.py 합성 음성으로 음성 라벨 인식률 평가
├─ demo.bat  train.bat  시연 / 학습 실행 파일
└─ models/  logs/  recordings/  dataset/  data/  runs/  scratch/   (git 제외)
```

## 4. 처리 흐름 (프레임 1장)

```
MjpegStream(스레드) ─ 최신 JPEG ─▶ Pipeline.process(frame, t)
  1. Detector: YOLO26s @640 + ByteTrack → [Detection(tid, name, box)]
  2. LaneDetector → 자차 차선 다각형 (미검출 시 기본 사다리꼴), 이탈 여부
  3. EgoMotion: 객체 영역을 가린 LK 광류 중앙값 → 자차 stopped / moving
  4. 이동 경로 기록 (박스 하단 중심, 트랙당 40개)
  5. RiskAnalyzer: 차량별 폭·오프셋 이력 → 앞차(자차 차선 안에서 가장 가까운 차) 선택
  6. 객체별:
     - traffic_light → classify_light(크롭) → FlashTracker → StateSmoother → 로그/음성(관련 신호등만)
     - vehicle       → VehicleLightTracker → StateSmoother → 로그 / 앞차면 음성
                     → cut_in 판정 (모든 차량)
                     → 앞차면 TTC 충돌 단계, 선행차 상태, 번호판(백그라운드)
  7. 주변 상황 집계 (차량·보행자·이륜차) → 1초 유지 시 로그, 20초마다 음성
  8. 사라진 트랙 정리 (3초)
Dashboard.render() → OpenCV 창 (--show)
```

### 시간 처리 원칙

- 모든 시간 판단(평활화, 점멸, 깜빡임, TTC)은 **프레임 도착 시각**을 기준으로 합니다.
- Wi-Fi 때문에 프레임 간격이 고르지 않아도 동작합니다.
- 처리가 느리면 중간 프레임을 버리고 항상 최신 프레임을 처리합니다.

## 4-1. 학습 데이터 수집과 학습 흐름

```
운전 중 (demo.bat = --show --label-voice)
  카메라 ─MJPEG─▶ MjpegStream ─원본 JPEG─▶ Recorder: recordings/<세션>/000123.jpg + timestamps.csv
                              └─프레임─▶ Pipeline ─관련 신호등 박스·판별값─▶ LabelSession 버퍼(최근 약 3.6초)
  마이크 ─▶ VoiceLabeler(Vosk, 문장 목록 모드)
            · 안내 음성 재생 중(+0.8초)에는 오디오 폐기
            · 발화 전체가 라벨 문장과 정확히 같을 때만 채택
          ─ VoiceLabel(시작 시각, 라벨) ─▶ LabelSession
            · 발화 시작 −1.0초 ~ +0.6초 프레임에서 신호등 크롭 최대 8장
            · 여백 15% + 정사각 레터박스 96px → crops/<클래스>/<이벤트ms>_<트랙>_<k>.jpg
            · labels.csv 한 줄 기록, "빨간불 5장 저장" 음성 확인
            · "취소"는 직전 라벨 삭제, "맞아"는 현재 판별값을 라벨로 사용
시연 후 (train.bat)
  build_tl_dataset.py : recordings/*/crops + data/extra_tl → dataset/tl_cls/{train,val}
                        (같은 라벨 이벤트는 한쪽에만 들어가게 분할, 20:80)
  train_tl.py         : YOLO26n-cls 96px, 색상 보존 증강(hue 변화·좌우반전 없음, 크롭 90~100%)
                        → models/tl_cls.pt + tl_cls.json(top1, 클래스 목록)
다음 실행
  Pipeline → TLClassifier(확신도 0.6 이상) 우선, 미만이면 규칙 판별 → FlashTracker → StateSmoother
```

- 점멸 라벨(`flashing_*`)은 **크롭을 저장하지 않고** labels.csv에 이벤트만 기록합니다.
  - 저장 구간에 꺼진 순간의 프레임이 섞여 프레임 단위 클래스를 오염시키기 때문입니다.
  - 점멸 판정은 계속 시간축(FlashTracker)으로 합니다.
  - 기록된 이벤트는 FlashTracker 임계값 검증에 씁니다.

## 5. 핵심 알고리즘 매개변수

| 모듈 | 규칙 | 기본값 |
|---|---|---|
| StateSmoother | 창 안에서 가중 다수결로 과반을 차지하고, `hold_s` 동안 유지되어야 상태 확정 | 신호등 창 1.0초 / 유지 0.3초, 등화 0.8/0.3, 선행차 1.2/0.6, 자차 1.5/1.0, 차선 1.5/1.0 (70%) |
| 신호등 | 장축/단축 비율 ≥ 3.4 → 4구. 칸별 밝은 픽셀(V≥170) ≥ 8% → 켜짐. 색상이 위치와 모순되면 제외 | |
| 점멸 | 3초 창에서 켜짐↔꺼짐 전환 ≥ 3회 | `flashing_yellow` / `flashing_red` |
| 브레이크 | 좌우 램프의 적색 점유율 ≥ 0.04, 그리고 비제동 기준선(하위 25%) × 1.8 + 0.01 이상 | |
| 방향지시등 | 2.5초 창, 10~90% 백분위 폭 ≥ 0.02, 전환 ≥ 3회, 0.6~3Hz. 양쪽 동시 → hazard | |
| TTC | ln(폭)의 0.8초 최소제곱 기울기 k → TTC = 1/k (k > 0.02) | 주의 < 2.7초, 위험 < 2.0초. 박스 폭이 화면의 6% 이상일 때만 |
| 앞차 | 박스 하단 중심의 차선 오프셋 \|o\| ≤ 0.8인 차 중 하단이 가장 아래인 차 | |
| 끼어들기 | 0.5초 이상 \|o\| ≥ 1.15였다가, 최근 4샘플 동안 중심으로 접근하고, \|o\| ≤ 0.75 | 트랙당 쿨다운 6초 |
| 선행차 상태 | 자차 정지: 후퇴(k < −0.04) → 2초간 starting, 이후 moving. \|k\| < 0.03 → stopped. 자차 주행: k > 0.12 → slowing, 그 외 moving | |
| 자차 정지 | 320px 폭 기준 광류 중앙값 < 4 px/s (1초 창) | |
| 번호판 | 앞차 폭이 화면의 18% 이상일 때 1초마다 백그라운드 OCR. 같은 번호 2회 → 확정 | |

## 6. 로그 형식

- 콘솔과 `logs/events_YYYYMMDD_HHMMSS.log`에 기록합니다. 파일에는 시각이 앞에 붙습니다.
- 형식:

  ```
  id : [이름] : "상태"
  ```

- 예시:

  ```
  3 : [traffic_light] : "red_left"
  7 : [car] : "brake_on,left_turn"
  7 : [car] : "lead_starting"
  7 : [car] : "collision_danger ttc=1.6s"
  7 : [license_plate] : "12가3456"
  9 : [truck] : "cut_in_right"
  0 : [lane] : "departure_left"
  0 : [ego] : "stopped"
  0 : [scene] : "vehicles=2,person=1,two_wheeler=0"
  ```

- id 0은 장면 단위 항목(ego, lane, scene)에 씁니다.
- 한 객체의 서로 다른 사실(등화, 주행, 충돌, 끼어들기)은 **채널**로 나눠 각각 중복을 억제합니다.

## 7. 음성 우선순위

| 우선순위 | 예 | 동작 |
|---|---|---|
| 0 DANGER | "전방 충돌 위험! 브레이크!" | 현재 발화를 끊고 즉시 안내 |
| 1 WARNING | 충돌 주의, 끼어들기, 차선 이탈, 앞차 브레이크 | 덜 급한 발화를 끊음 |
| 2 SIGNAL | 신호등 변화, 방향지시등, 앞차 출발·정지 | |
| 3 INFO | 번호판, 주변 상황, 시작 안내 | |

- 키별 쿨다운(기본 8초, 항목별 재정의)을 적용합니다.
- 2.5초 넘게 대기한 안내는 폐기합니다.

## 8. 운전 상황 모니터 (`--show`)

- 크기: 1440×910
- 배치:
  - **상단:** 음성 자막을 큰 글씨로 5초간 표시합니다. 위험도별 색은 빨강(위험), 주황(경고), 파랑(신호), 회색(정보)입니다.
  - **좌측:** 실시간 영상. 자차 차선 영역, 진행 방향 화살표, 객체 ID, 앞차(LEAD) 강조, 차량별 **이동 경로 궤적**을 그립니다.
  - **우측:** 신호등 램프와 상태, 위험 감지(안전/주의/위험, TTC 게이지, 최근 위험 이벤트), 앞차(ID, 주행 상태, **번호판**, 브레이크·좌·비상·우 표시), 현재 차선(차선 안 자차 위치 그림)과 자차 상태, 분석 결과 표(ID/객체/상태/번호판).
  - **하단:** 최근 이벤트 로그, 시각, fps, 자차 상태.
- 충돌 "위험"이면 화면 테두리가 빨갛게 깜빡입니다.
