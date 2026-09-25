# HANDOVER.md — 인수인계서

- 작성일: 2026-09-24
- 버전: v0.2 (v0.1 MVP + 녹화, 자동 탐색, 음성 라벨링, 학습, 시연 도구)
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
| `DEMO.md` | **차량 시연 절차** (전날 준비, 연결, 음성 라벨링, 학습, 문제 해결) |
| `app_spec.md` | 사용자 요구사항 (기능 F1~F10) |
| `research.md` | 인식 방법 조사, 근거 논문, 수치, 라이선스, 실측 벤치마크 |
| `architech.md` | 기술 스택, 폴더 구조, 처리 흐름, 알고리즘 매개변수, 로그 형식, 모니터 배치 |
| `dev_plan.md` | 마이크로 단계 개발 계획과 진행 상태 (Phase 0~6) |
| `history.md` | 작업 이력 (시간순) |
| `../esp32_cam/docs/` | 카메라 보드 분석, 펌웨어 빌드·업로드 이력 (별도 폴더, 이 저장소에 포함되지 않음) |

## 3. 현재 상태 (2026-09-24 22시 기준, v0.2)

| 항목 | 상태 |
|---|---|
| 단위·통합 테스트 | **79개 모두 통과** (`pytest -q`, 약 15초. 음성 통합 테스트 포함) |
| 실제 카메라 연동 | ✅ 스트림 수신, 카메라 설정 적용, 검출, 로그, 음성(Heami), 모니터 스냅샷 확인 |
| v0.4 (2026-09-25) | **네이티브 Windows 앱** `windows_app/` (C# WPF .NET 8, `CarSignalDetector.exe` 약 200KB). 엔진은 `python -m csd --serve`로 창 없이 실행하고, 앱이 `/state`·`/frame.jpg`로 표시하며 시작·정지와 옵션을 제어. `windows_app/README.md` |
| v0.3 (2026-09-25) | **카메라 영상을 USB 케이블로 전송**. 펌웨어 `usb_stream.cpp` + `csd/usb_camera.py`. XGA 27.7fps (Wi-Fi 9.2fps). 노트북 Wi-Fi는 인터넷용으로 유지. `transport: auto`는 USB를 먼저 찾고 없으면 Wi-Fi. 점검·녹화 도구도 USB 지원 |
| v0.2 추가 기능 | 녹화·재생(`tools/record.py`, `--record`, `--source`), 카메라 자동 탐색, 카메라 자체 Wi-Fi(AP) 펌웨어, **음성 라벨링**(`--label-voice`), 신호등 분류기 학습(`train.bat`), 출발 전 점검(`tools/preflight.py`), 시연 실행 파일(`demo.bat`) |
| 음성 라벨 인식 | 합성 음성 평가에서 라벨 21/21 정확, 일반 대화 15문장 중 오인 0 (`tools/eval_voice_labels.py`). **실제 사람 목소리·차량 소음에서는 미검증** |
| 학습 파이프라인 | 합성 크롭으로 데이터셋 → 학습(5에폭 약 1분) → 로드·예측까지 확인. 실제 데이터 학습은 아직 없음 |
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
# 네이티브 Windows 앱 (권장 화면): 빌드 후 exe 실행 → ▶ 시작
windows_app\build.bat
windows_app\publish\CarSignalDetector.exe            # --autostart 로 바로 시작

# 차량 시연: 점검 → 전체 화면 모니터(OpenCV) + 음성 + 녹화 + 음성 라벨링 (DEMO.md)
demo.bat

# 출발 전 점검만
.\.venv\Scripts\python.exe tools\preflight.py

# 녹화만 (검출 없이, 원본 JPEG + 시각)
.\.venv\Scripts\python.exe tools\record.py --seconds 600 --name daytime

# 녹화본 재생 (기록된 시각대로. --realtime이면 실시간처럼 늦은 프레임 건너뜀)
.\.venv\Scripts\python.exe -m csd --source recordings\<폴더> --show

# 녹화본을 보면서 음성 라벨링 (집에서 라벨 추가)
.\.venv\Scripts\python.exe -m csd --source recordings\<폴더> --realtime --show --label-voice

# 음성 라벨로 신호등 분류기 학습 → models\tl_cls.pt (다음 실행부터 자동 사용)
train.bat

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

0. **카메라 연결은 USB 케이블이 기본입니다**(v0.3).
   - 보드의 **네이티브 USB 포트**(`303A:1001`, 이 PC에서는 COM7)로 영상과 설정을 주고받습니다.
   - CH340 포트(COM6)는 펌웨어 업로드와 로그 전용입니다.
   - 네이티브 포트를 열 때 DTR/RTS를 끄고 엽니다. 켜면 칩이 리셋될 수 있습니다.
   - 한 번에 한 프로그램만 이 포트를 열 수 있습니다. 앱이 실행 중이면 녹화 도구나 점검 도구는 카메라를 찾지 못합니다.
1. **Wi-Fi 방식일 때 카메라 주소는 자동으로 찾습니다**(`csd/discover.py`). 순서는 다음과 같습니다.
   1. `camera.base_url`
   2. 192.168.4.1 (카메라 자체 Wi-Fi `CSD-CAM` / `csdcam1234`)
   3. esp32cam.local (현재 집 중계기 네트워크에서는 mDNS가 조회되지 않음)
   4. 노트북 /24 서브넷 검색
   - 카메라 부팅 로그(CH340 COM6, 115200)에 `Camera Ready! Use 'http://...'`가 나옵니다.
   - 카메라 펌웨어는 알려진 Wi-Fi(집, 선택적 `WIFI_SSID2`)에 각 12초씩 시도하고, 실패하면 AP를 엽니다.
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
| K9 | 번호판 개인정보 | 번호는 화면·로그에만 표시. **`--record`/`--label-voice` 녹화에는 번호판과 얼굴이 담김** (`recordings/`는 git 제외) | 공개 게시 금지. 로그·녹화 보관 기간 정책 필요 |
| K10 | 음성 라벨: Vosk 한국어 모델은 `[unk]`(모르는 말)을 지원하지 않음 | 방해 단어 목록과 "정확히 한 문장만" 규칙으로 막았지만, 실제 목소리·소음에서는 미검증 | 시연 녹화로 `labels.csv`를 확인하고 "취소" 사용 빈도 점검 |
| K11 | Vosk는 **한글 절대 경로의 모델을 열지 못함** | `ascii_model_path()`가 상대 경로를 쓰거나, 안 되면 `%PUBLIC%\csd_models`로 복사 | — |
| K12 | 마이크가 무음으로 측정됨 (2026-09-24 22시, rms 1) | 음성 라벨링 불가 | Windows 마이크 권한과 음소거 확인 (DEMO.md 0장) |
| K14 | USB 전송 상한 약 650KB/s (USB 1.1 Full-Speed) | 밝은 장면이나 고해상도에서는 프레임이 커져 fps가 낮아짐 (UXGA 11fps) | 실도로에서 XGA fps 확인. 필요하면 quality를 12→15로 |
| K15 | 해상도 전환 직후 JPEG 한 장이 깨질 수 있음 (`Corrupt JPEG data` 경고 1회) | 해당 프레임 한 장만 영향 | 무시 가능 |
| K13 | CameraWebServer 예제의 `camera_config_t config;` 초기화 누락 | 코드 배치가 바뀌자 `frame buffer malloc failed` 발생 | **수정함** (`= {}`). esp32_cam 이력 9장 |

## 7. 코드 읽는 순서 (추천)

1. `csd/app.py`: `Pipeline.process()`가 전체 흐름입니다.
2. `csd/state.py`: 모든 상태가 거치는 `StateSmoother`와 `EventLog`입니다.
3. 기능별 모듈:
   - `traffic_light.py`, `vehicle_lights.py`, `risk.py`, `lanes.py`, `ego_motion.py`, `plate.py`
4. 출력 쪽 모듈:
   - `messages.py`(문구) → `tts.py`(음성) → `dashboard.py`/`overlay.py`(화면)
5. 데이터 수집·학습 모듈:
   - `voice_label.py`(음성 인식) → `labeling.py`(크롭 저장) → `tools/build_tl_dataset.py` → `tools/train_tl.py` → `tl_classifier.py`(앱에서 사용)
6. 입력·연결 모듈:
   - `discover.py`(카메라 찾기), `recorder.py`(녹화), `stream.py`(수신·재생)
7. `tests/`: 모듈별 사용 예시와 기대 동작을 보여줍니다.

## 8. 다음 담당자가 바로 할 일 (우선순위)

1. **차량 시연** (`DEMO.md`): 노트북을 충전하고 마이크 권한을 확인한 뒤 `demo.bat` 실행. 음성 라벨로 신호등 데이터 수집
2. 시연 녹화본을 `--source`로 재생하며 오탐 목록 작성 (dev_plan 3.4)
3. `train.bat`로 첫 신호등 분류기 학습 → 녹화본 재생으로 규칙 판별과 비교
4. 카메라 노출과 해상도 튜닝, `horizon_ratio` 보정 (dev_plan 3.5~3.6)
5. AI Hub 신호등 데이터 신청 → `data\extra_tl\<클래스>\`에 크롭 추가 (dev_plan 4.1)
