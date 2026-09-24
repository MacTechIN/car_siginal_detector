# HANDOVER.md — 인수인계서

- 작성일: 2026-09-24
- 버전: v0.1 (MVP)
- 저장소: https://github.com/MacTechIN/car_siginal_detector

이 문서만 읽고 이어서 작업할 수 있도록 현재 상태, 실행 방법, 환경의 특이사항, 알려진 문제, 다음 할 일을 정리했습니다.

## 1. 한 줄 요약

ESP32-S3 카메라(OV3660)의 MJPEG 영상을 PC가 받아서 다음을 인식합니다.
- 한국 신호등 상태
- 앞차 번호판·브레이크등·방향지시등·주행 상태
- 차선 이탈, 충돌 위험(TTC), 끼어들기, 주변 상황

결과는 `id : [이름] : "상태"` 로그, **한국어 음성**, **운전 상황 모니터 창**으로 알려줍니다.

## 2. 문서 지도

| 문서 | 내용 |
|---|---|
| `app_spec.md` | 사용자 요구사항 (기능 F1~F10) |
| `research.md` | 인식 방법 조사, 근거 논문, 수치, 라이선스, 실측 벤치마크 |
| `architech.md` | 기술 스택, 폴더 구조, 처리 흐름, 알고리즘 매개변수, 로그 형식, 모니터 배치 |
| `dev_plan.md` | 마이크로 단계 개발 계획과 진행 상태 (Phase 0~6) |
| `history.md` | 작업 이력 (시간순) |
| `../esp32_cam/docs/` | 카메라 보드 분석, 펌웨어 빌드·업로드 이력 (별도 폴더, 이 저장소에 포함되지 않음) |

## 3. 현재 상태 (2026-09-24 기준)

| 항목 | 상태 |
|---|---|
| 단위 테스트 | **48개 모두 통과** (`pytest -q`, 약 3.5초) |
| 실제 카메라 연동 | ✅ 스트림 수신, 카메라 설정 적용, 검출, 로그, 음성(Heami), 모니터 스냅샷 확인 |
| 처리 속도 | 배터리·균형 모드 기준 **약 2.7~4.2 fps** (yolo26s@640, XGA 스트림) |
| 실제 도로 검증 | ❌ **아직 하지 않았습니다.** 모두 실내와 합성 데이터로만 확인했습니다. 신호등, 번호판, 등화, 차선, 끼어들기의 실도로 정확도는 모릅니다. |
| 카메라 펌웨어 | CameraWebServer (Wi-Fi 정보는 `../esp32_cam/firmware/camera_web_server/wifi_secrets.h`, 저장소에 포함하지 않음) |

## 4. 실행 방법

### 4.1 처음 설치

```powershell
cd C:\Users\이상진\Documents\workspace\car_siginal_detector
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe tools\fetch_models.py --detector yolo26s yolo26n
```

- 모델은 `models/`에 받습니다(git 제외).
  - `yolo26s.pt`, `yolo26n.pt`
  - `plates/*.pt` 3개, 약 280MB

### 4.2 실행

```powershell
# 카메라 + 음성 + 모니터 창 (q 또는 Esc로 종료)
.\.venv\Scripts\python.exe -m csd --show

# 음성 없이 30초만 실행하고 마지막 화면 저장
.\.venv\Scripts\python.exe -m csd --no-voice --seconds 30 --snapshot scratch\screen.png

# 녹화 영상이나 이미지 폴더 재생
.\.venv\Scripts\python.exe -m csd --source path\to\video.mp4 --show

# 설정 덮어쓰기 (IP, 모델, 해상도 등)
.\.venv\Scripts\python.exe -m csd --config my.yaml
```

- 콘솔에 한글이 깨지면 `$env:PYTHONIOENCODING="utf-8"`를 먼저 실행합니다.
- 로그는 콘솔과 `logs/events_*.log`에 남습니다.

### 4.3 테스트

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest tests\test_risk.py -q       # 한 파일만
.\.venv\Scripts\python.exe -m pytest -q -k cut_in               # 이름으로 선택
```

## 5. 환경 특이사항 (반드시 읽기)

1. **카메라 IP:** `192.168.45.138`은 DHCP로 받은 주소라 바뀔 수 있습니다. 바뀌면 `configs/default.yaml`의 `camera.base_url`과 `camera.stream_url`을 고칩니다.
   - 새 IP는 카메라 부팅 로그(CH340 COM6, 115200)에 `Camera Ready! Use 'http://...'`로 나옵니다.
2. **ESP32 펌웨어 빌드는 한글 사용자 경로에서 실패합니다.** Xtensa 링커가 비ASCII 경로를 처리하지 못하기 때문입니다.
   - 전용 코어 `C:\arduino-esp32`를 사용합니다.
   - 명령마다 `$env:ARDUINO_DIRECTORIES_DATA="C:\arduino-esp32"`를 지정하고 `--build-path`에는 ASCII 경로를 줍니다.
   - 정션(심볼릭 링크)은 arduino-cli가 처리하지 못합니다.
   - 자세한 내용은 `../esp32_cam/docs/history.md` 6-4~6-5를 참고하세요.
3. **업로드는 보드의 CH340 USB 포트(COM6)로 합니다.** 자동 리셋이 됩니다.
   - 네이티브 USB 포트(COM5)는 BOOT 버튼을 눌러야 합니다.
   - CH340으로 esptool `read-flash`를 한 번에 길게 읽으면 데이터가 깨집니다. 64KB씩 나눠 읽으세요.
4. **Wi-Fi는 2.4GHz만 됩니다.**
5. **이 PC에는 NVIDIA GPU가 없습니다**(i5-1340P + Iris Xe). 배터리 모드에서는 CPU 클럭이 1.5GHz로 내려가 fps가 크게 떨어집니다. 차량에서는 AC 전원과 고성능 모드를 권장합니다.
6. **공장 펌웨어 백업:** `../esp32_cam/backup/factory_led_strip_2MB.bin` (SHA256 `ad7a70cb…fe65726`, 칩과 일치 검증).

## 6. 알려진 문제와 한계

| # | 문제 | 영향 | 대응 방향 |
|---|---|---|---|
| K1 | 실도로 검증을 하지 않음 | 임계값(색상, 점유율, TTC, 끼어들기 오프셋)이 합성 데이터 기준 | dev_plan Phase 3 |
| K2 | **어둡거나 노이즈가 많은 영상에서 자차 "주행" 오판** (광류 노이즈) | 선행차 상태 판단이 틀림 | 광류 품질 필터(특징점 수, 순방향·역방향 일관성), GPS 연동 |
| K3 | 실내에서 차선이 오검출되고 이탈이 깜빡임 | 평활화(1초 유지, 70%)로 완화했지만 근본 해결은 아님 | TwinLiteNet, 거치 후 `horizon_ratio` 보정 |
| K4 | 신호등 판별이 규칙 기반 | 역광, 원거리, 세로형, 보행자 신호에 약함. 보행자 신호등도 COCO "traffic light"로 검출되어 섞일 수 있음 | AI Hub로 크롭 분류기 학습 (Phase 4.1) |
| K5 | 앞차 "정지/주행"은 상대 운동만으로 판단 | 자차 주행 중에는 moving/slowing만 구분 | GPS/OBD 속도 연동 |
| K6 | 번호판 모델이 무거움 (vertex·syllable 모델 각 137MB, 첫 호출 약 1.6초) | 백그라운드 스레드로 분리해 메인 루프는 막지 않음 | ONNX, 입력 크기 축소 |
| K7 | OV3660 영상이 실내에서 매우 어두움 (`ae_level=-1` 영향 포함) | 실내 테스트 품질이 낮음 | 주간 실외에서 재튜닝 |
| K8 | **라이선스:** Ultralytics YOLO는 AGPL-3.0 | 배포하면 소스 공개 의무 | 공개 유지 또는 RF-DETR/D-FINE(Apache)로 교체 (Phase 6.1) |
| K9 | 번호판 개인정보 | 번호는 화면·로그에만 표시, 영상 미저장 | 공개 게시 금지. 로그 보관 기간 정책 필요 |

## 7. 코드 읽는 순서 (추천)

1. `csd/app.py`: `Pipeline.process()`가 전체 흐름입니다.
2. `csd/state.py`: 모든 상태가 거치는 `StateSmoother`와 `EventLog`입니다.
3. 기능별 모듈:
   - `traffic_light.py`, `vehicle_lights.py`, `risk.py`, `lanes.py`, `ego_motion.py`, `plate.py`
4. 출력 쪽 모듈:
   - `messages.py`(문구) → `tts.py`(음성) → `dashboard.py`/`overlay.py`(화면)
5. `tests/`: 모듈별 사용 예시와 기대 동작을 보여줍니다.

## 8. 다음 담당자가 바로 할 일 (우선순위)

1. **카메라를 차량에 거치**하고 AC 전원으로 `--show` 실행 → 주간 도로 10분 확인
2. `tools/record.py` 작성(녹화) → 녹화본으로 `--source` 재생하며 오탐 목록 작성 (dev_plan 3.2~3.4)
3. 카메라 노출과 해상도 튜닝, `horizon_ratio` 보정 (dev_plan 3.5~3.6)
4. AI Hub 신호등 데이터 신청과 크롭 분류기 학습 (dev_plan 4.1)
