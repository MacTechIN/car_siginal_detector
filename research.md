# research.md — 사물(신호·차량 상태) 인식 방법 조사

- 조사일: 2026-09-24
- 조사 방식: 에이전트 6개를 동시에 실행하는 오케스트레이션. 각 에이전트는 WebSearch/WebFetch로 원문을 확인했습니다.

| # | 조사 영역 | 주요 출처 |
|---|---|---|
| 1 | GitHub 오픈소스 | 신호등 / 후미등 / ESP32-CAM 파이프라인 저장소 |
| 2 | Hugging Face | 모델·데이터셋·라이선스 |
| 3 | 논문·Google 검색 | SOTA 방법론 (arXiv, IEEE, MDPI) |
| 4 | Espressif 공식 | ESP-DL / ESP-Detection / esp32-camera / Arduino-ESP32 |
| 5 | 한국 특화 | 한국 신호등, AI Hub, 번호판, 한국어 TTS |
| 6 | ADAS | 차선, 충돌 경고(FCW), 끼어들기, 선행차 상태 |

표기 규칙: 원문에서 확인하지 못한 내용은 **[미검증]** 으로 표시합니다.

---

## 1. 결론 요약

| 기능 | 채택한 방법 (v0.1) | 더 정확한 다음 단계 |
|---|---|---|
| 추론 위치 | **PC에서 추론.** ESP32-S3는 촬영과 스트리밍만 담당 | — |
| 객체 검출·추적 | COCO 사전학습 **YOLO26s** + **ByteTrack** | 한국 도로 영상으로 파인튜닝 |
| 신호등 상태 | 검출 박스를 잘라 **램프 위치와 색상으로 규칙 판별**(한국 가로형 3·4구) → 시간축 투표 → 점멸 판정 | AI Hub 신호등 데이터로 **크롭 분류기**(MobileNetV2/ConvNeXt-T) 학습 |
| 브레이크등·방향지시등 | 차량 박스의 좌우 램프 영역에서 **빨강·호박색 점유율**을 측정. 브레이크는 트랙별 기준선 대비, 방향지시등은 **0.6~3Hz 깜빡임** 판정 | TLD 데이터셋 방식(ResNet 분류 + 시간 필터) 또는 CNN-LSTM |
| 번호판 | sauce-Git **plate → vertex(원근보정) → syllable** YOLO 3단계, 정규식 검증, 여러 프레임 투표 | PaddleOCR 한국어로 교차 검증 |
| 차선 | **고전적 방법(HLS 색 마스크 + Canny + Hough)**, 중앙에 가장 가까운 선을 선택, EMA 평활 | TwinLiteNet(+) 또는 UFLDv2를 OpenVINO로 |
| 충돌 경고 | 박스 폭의 **스케일 변화율로 TTC 계산** (Mobileye 방식): 2.7초 주의, 2.0초 위험 | 지면 기하 거리 + Kalman 필터 |
| 끼어들기 | 차선 중심 대비 **횡방향 오프셋 이력**: 밖(≥1.15)에 0.5초 이상 있다가 안(≤0.75)으로 접근 | 방향지시등 신호와 결합, PREVENTION 데이터로 튜닝 |
| 선행차 주행 상태 | 자차 정지/주행(배경 광류) × 선행차 스케일 변화 → 정지·출발·주행·감속 | GPS/OBD 자차 속도 연동 |
| 음성 | **Windows SAPI5 + Microsoft Heami (한국어, 오프라인)**, 우선순위 큐, 중복 억제, 오래된 안내 폐기 | NaturalVoiceSAPIAdapter로 SunHi 자연음성 사용 |

---

## 2. 추론 위치: ESP32 온디바이스 vs PC

Espressif 공식 벤치마크 (esp-dl `models/coco_detect`, esp-detection):

| 모델 (ESP32-S3, INT8) | 입력 | mAP50-95 | 프레임당 시간 |
|---|---|---|---|
| YOLO11n | 640×640 | 0.370 | **약 26초** |
| YOLO11n | 320×320 | 0.276 | 약 6.2초 |
| espdet_pico (고양이 1클래스) | 224×224 | 69.9 | 126 ms |
| pedestrian pico | 224×224 | — | 약 118 ms |

- ESP-DL / ESP-Detection은 **ESP-IDF 5.3 이상**이 필요합니다.
- Arduino 코어 3.3.x에서는 공식 지원을 확인하지 못했습니다 [미검증].
- 결론: 멀리 있는 작은 신호등을 224px 입력으로 인식하는 것은 비현실적입니다. 그래서 **PC에서 추론**하기로 했습니다.

출처:
- https://github.com/espressif/esp-dl
- https://github.com/espressif/esp-detection

---

## 3. 신호등 인식

### 3.1 한국 신호등 구조

- 차량 신호등은 대부분 **가로형**이고, 왼쪽부터 읽습니다.
  - 3구: 적 · 황 · 녹
  - 4구: 적 · 황 · **좌회전 화살표** · 녹
- 상태 조합: 적, 황, 녹, 녹+좌회전(동시신호), 적+좌회전, 황색 점멸, 적색 점멸.
- 보행자·버스·자전거 신호는 별도입니다.
- 출처: ko.wikipedia "대한민국의 신호등", 나무위키

### 3.2 방법별 정확도

| 방법 | 근거 | 수치 |
|---|---|---|
| 단일 단계 YOLO + 형태×상태 결합 클래스 + 시간 버퍼 | ATLAS (Polley et al., IEEE IV 2025) https://arxiv.org/abs/2504.19722 | YOLO11x mAP50 0.72 / mAP50-95 0.54. 폭 2~4px까지 검출. 검출 46ms, 버퍼 포함 184ms |
| 검출 → 크롭 재분류 (2단계) | Pavlitska et al. 서베이 (ITSC 2023) https://arxiv.org/abs/2309.02158 | 해상도가 낮은 입력에 유리 |
| SAHI 타일 추론 | https://arxiv.org/abs/2202.06934 | 항공영상 기준 AP +5~7 (추론만), 파인튜닝 시 +12~14 |
| HSV 색 휴리스틱 | 최근 논문 수치 없음 | 보조·검증용 |
| KASTEL 저장소 (YOLOv8, DTLD) | https://github.com/KASTEL-MobilityLab/traffic-light-detection (AGPL-3.0) | YOLOv8-XL mAP50 0.82 |

### 3.3 LED 깜빡임(PWM)과 노출

- LED 신호는 100Hz~1kHz PWM으로 켜집니다. 노출이 짧으면 프레임에서 "꺼짐"으로 찍힙니다.
  - 출처: IS&T EI 2018 "LED flicker: root cause, impact and measurement"
- 대책:
  - 노출을 10ms 이상으로 유지합니다.
  - 노출과 이득을 약간 낮춰 램프 색이 흰색으로 날아가지 않게 합니다.
  - **여러 프레임으로 투표**합니다.
  - 상태 필터링에는 HMM을 쓰는 방법도 있습니다(aUToLights 2023, https://arxiv.org/abs/2305.08673).

### 3.4 데이터셋과 라이선스

| 데이터셋 | 규모·클래스 | 라이선스 |
|---|---|---|
| **AI Hub 신호등/도로표지판 인지 영상 (수도권)** dataSetSn=188 | 약 190만 장, 신호등 박스 300만 개 | **내국인만 신청 가능.** R&D 이용 가능, 상용은 별도 협의, 재배포 금지 |
| **AI Hub 신호등 신호정보 인지 영상** dataSetSn=71579 | 약 10만 장 1920×1080, 점멸 포함 | 위와 같음 |
| S2TLD | 5,786장, red/yellow/green/off/wait-on | **MIT (상용 가능)** |
| DTLD | 23만 개 이상 | 등록 필요, 비상업용 [원문 미검증] |
| LISA / BSTLD / VZC | — | 비상업용 |
| ATLAS (Zenodo 14775869) | 33k장, 형태×상태 25클래스 | 확인 필요 |

- Autoware `traffic_light_classifier` (MobileNetV2, Apache-2.0)은 크롭 분류기로 쓸 수 있습니다. 99.81%는 일본 내부 데이터 기준이라 한국 도로에서의 성능은 다를 수 있습니다.
- **한국 신호등으로 학습된 공개 모델은 찾지 못했습니다.** AI Hub 데이터로 직접 학습해야 합니다.

---

## 4. 차량 등화 (브레이크 / 방향지시등 / 비상등)

| 방법 | 근거 | 수치 |
|---|---|---|
| 검출 → 추적 → 브레이크·방향 분류기 2개 + 시간 필터 | **TLD** (2024) https://arxiv.org/abs/2409.02508, HF `ChaiJohn/TLD` | 브레이크 F1 96.84, 방향지시등 F1 86.82 (켜짐 0.1s, 꺼짐 0.6s 필터) |
| 후미등 분할 + C3D(16프레임) | Seo et al., Sensors 2024 | UC Merced 85.19% (CNN-LSTM 62.12%) |
| CNN-LSTM + 어텐션 | arXiv 1906.03683 | 수치 미공개 |
| 밝기 시계열 주파수 분석 | IEEE IV 2014 | 방향지시등 **1.5 ± 0.5 Hz** |

- 공개 가중치가 있는 후미등 모델은 **찾지 못했습니다.**
- 깜빡임을 판정하려면 최소 5fps 이상에서 약 2초 창이 필요합니다(나이퀴스트).
- 한국 차량 중에는 **적색 후면 방향지시등**도 있습니다. 그래서 구현에서는 깜빡임 판정에 호박색과 적색(0.5 가중)을 함께 씁니다.

---

## 5. ESP32-S3 + OV3660 카메라 설정

- OV3660 최대 프레임률(데이터시트): QXGA 15fps, 1080p 20fps, XGA/720p 45fps, VGA 60fps.
- 실제로는 Wi-Fi와 JPEG 인코딩이 병목입니다. 커뮤니티 보고로는 UXGA에서 약 3~5fps입니다 [미검증].
- 픽셀 예산: 폭 30cm 램프가 50m 거리에 있을 때 640px 폭에서 약 4px, 1600px에서 약 10px입니다(조사 에이전트 추정).
- **v0.1 기본값:** XGA(1024×768), quality 12, `ae_level=-1`, `wb_mode=1`(맑음), `gainceiling=2`, `saturation=1`.
- CameraWebServer 제어 API:
  - 설정 변경: `GET /control?var=<name>&val=<n>`
  - 상태 조회: `/status`
  - 스트림: `:81/stream`
  - 출처: https://github.com/espressif/arduino-esp32/blob/master/libraries/ESP32/examples/Camera/CameraWebServer/app_httpd.cpp
- `framesize` 번호는 설치된 헤더에서 직접 확인했습니다 (`esp32-camera/driver/include/sensor.h`, 코어 3.3.12).
  - VGA=10, SVGA=11, XGA=12, HD=13, SXGA=14, UXGA=15, FHD=16, QXGA=19
- Wi-Fi 지연을 줄이려면 `WiFi.setSleep(false)`를 설정합니다(예제에 이미 적용됨).
- 고정 IP는 `WiFi.config(...)`를 `WiFi.begin()` 전에 호출해 설정합니다. mDNS는 `MDNS.begin("esp32cam")`을 씁니다.

---

## 6. 검출기 비교 (COCO)

| 모델 | COCO AP | 라이선스 | 비고 |
|---|---|---|---|
| YOLO26 n~x | 40.1~56.9 | AGPL-3.0 | Ultralytics. CPU ONNX 39~526ms (Ultralytics 문서) |
| RF-DETR N~L | 48.4~56.5 | **Apache-2.0** | 상용 배포 시 대안 |
| D-FINE N~X | 42.8~59.3 | **Apache-2.0** | |
| DEIMv2 | 최대 57.8 | 비상업용 | |

- 영지식(zero-shot) 검출과 VLM 검증 후보: Grounding DINO, OWLv2, Florence-2, Qwen3-VL / Qwen3.5 (Apache/MIT).
  - GPU가 없는 이 PC에서는 제외했습니다.

### 이 PC에서 직접 측정한 속도 (2026-09-24)

- 조건: i5-1340P, **배터리 전원·균형 조정 모드(1.5GHz)**, 800×600 프레임.

| 모델 | PyTorch | OpenVINO | ONNX Runtime |
|---|---|---|---|
| yolo26n @640 | 121 ms | 110 ms | 111 ms |
| yolo26s @640 | 240 ms | 381 ms | 232 ms |
| yolo26s @960 | 490 ms | 1157 ms | — |
| yolo26m @960 | 1303 ms | 3065 ms | — |

- 이 조건에서는 OpenVINO가 오히려 느렸습니다. 전원 모드 영향으로 보이며, **AC 전원에서 다시 측정해야 합니다.**
- 전체 파이프라인(yolo26s@640, XGA 스트림)은 **약 2.7~4.2 fps**였습니다.

---

## 7. 차선 · 충돌 · 끼어들기 · 선행차 상태

### 차선 모델

| 모델 | 정확도 | 비고 |
|---|---|---|
| UFLDv2 R18/R34 | CULane F1 75.0 / 76.0 | MIT, PINTO 모델 저장소에 ONNX·OpenVINO 있음 |
| CLRNet / CLRerNet | CULane F1 79.6 / 81.1 | 무거움, ONNX 변환 어려움 |
| YOLOPv2 | lane IoU 27.2, drivable mIoU 93.2 | |
| TwinLiteNet(+) | drivable 91.3~92.9, lane IoU 31~34 | MIT, 0.4M 파라미터, `pretrained/best.pth` 공개(GitHub 확인) |

v0.1은 CPU 부담이 없는 고전적 방법을 씁니다. 다음 단계에서 TwinLiteNet을 OpenVINO로 적용합니다.

### 충돌 경고 (FCW)

- **TTC = 1 / (d ln w / dt)**
  - 출처: Dagan, Mano, Stein, Shashua, "Forward collision warning with a single camera", IEEE IV 2004
- 임계값 근거:
  - NHTSA NCAP FCW 시험은 TTC 2.0~2.4초 이상에서 경고해야 합니다.
  - Mobileye는 "최대 2.7초 전"에 경고한다고 홍보합니다.
  - 그래서 **주의 2.7초 / 위험 2.0초**로 정했습니다.
- 거리 추정 공식(향후 적용):
  - 지면 기하: Z ≈ f·H / (y_bottom − y_horizon)
  - 차폭 기준: Z ≈ f·1.8m / w_px

### 끼어들기

- PREVENTION 데이터셋이 있습니다(356분, 차선변경 1~2초 전 예측 정확도 90% 이상).
- v0.1은 규칙 기반입니다. 자세한 조건은 architech.md를 참고하세요.

### 선행차 정지·출발

- 자차가 정지해 있을 때는 선행차 스케일 변화만으로 신뢰할 수 있습니다.
- 자차가 주행 중일 때는 상대 운동만 보이므로 "주행 / 감속"만 구분합니다.
- 정확히 구분하려면 GPS나 OBD로 자차 속도를 받아야 합니다.
- 자차 정지 판정은 배경 광류의 중앙값으로 합니다.
  - 참고: MDPI Electronics 2024, 13(20) 4097

---

## 8. 한국어 음성 (TTS)

| 후보 | 특성 | 채택 |
|---|---|---|
| Windows SAPI5 **Microsoft Heami** | 오프라인, 지연 짧음. 이 PC에 설치됨 (확인) | **주 엔진** |
| NaturalVoiceSAPIAdapter (MIT) | Narrator 자연음성(SunHi/InJoon)을 SAPI로 사용 | 음질 개선 옵션 |
| MeloTTS-Korean (MIT) | CPU 실시간 가능(제3자 주장), 설치가 무거움 | 대체안 |
| Piper | 공식 한국어 음성 없음. 커뮤니티 KSS 모델은 비상업용 | 제외 |
| edge-tts | 온라인 전용 | 제외 |

- 안내 정책:
  - 우선순위 큐를 씁니다: 위험 > 경고 > 신호 > 정보.
  - 더 급한 안내는 현재 발화를 끊습니다(`SVSFPurgeBeforeSpeak`).
  - 같은 키의 안내는 쿨다운 동안 반복하지 않습니다.
  - 2.5초 넘게 기다린 안내는 폐기합니다.

---

## 9. 번호판

- 형식:
  - `12가3456` (7자리)
  - `123가4567` (8자리, 2019년 이후)
  - 구형 지역 표기 `서울12가3456`
  - 정규식: `^(?:[가-힣]{2})?\d{2,3}[가-힣]\d{4}$`
- 채택: https://github.com/sauce-Git/korean-license-plate-detector (Apache-2.0)
  - 가중치: https://huggingface.co/sauce-hug/korean-license-plate-detector (HF 카드에는 라이선스 표기가 없음)
  - 글자 클래스 75개. 원본 `kor_list.txt`와 순서가 일치하는 것을 확인했습니다.
- 대안:
  - PaddleOCR `korean_PP-OCRv5_mobile_rec` (Apache-2.0, 일반 텍스트 88%)
  - AI Hub 차량 번호판 데이터 (dataSetSn=172)
- **개인정보:**
  - 번호판 번호만으로는 일반적으로 개인정보가 아니지만, 다른 정보와 결합하면 개인정보가 됩니다.
  - 개인정보보호법 제25조의2(2023 시행)는 업무 목적의 이동형 촬영에 고지 의무를 둡니다.
  - 본 프로젝트의 원칙:
    - 번호는 로그와 화면에만 표시합니다.
    - 영상과 크롭은 저장하지 않습니다.
    - 온라인에 게시하지 않습니다.

---

## 10. 라이선스 요약

| 구분 | 항목 |
|---|---|
| 상용 사용 가능 | RF-DETR N~L, D-FINE, RT-DETRv2, Grounding DINO, OWLv2, Qwen3-VL (Apache-2.0); Florence-2, S2TLD, TwinLiteNet, UFLDv2 (MIT); sauce-Git 코드 (Apache-2.0) |
| 조건부 | **Ultralytics YOLO = AGPL-3.0.** 배포하려면 소스를 공개하거나 상용 라이선스를 사야 합니다. 이 프로젝트는 현재 이 조건에 해당합니다. |
| 연구용만 | BSTLD, LISA, DTLD, VZC, DEIMv2, TLD(라이선스 미표기) |
| 별도 협의 | AI Hub 데이터 (R&D 가능, 상용은 협의, 재배포 금지, 내국인만 신청) |
