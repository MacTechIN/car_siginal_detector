@echo off
rem ===== car_siginal_detector 차량 시연 =====
rem 1) 출발 전 점검  2) 모니터 전체 화면 + 음성 안내 + 녹화 + 음성 라벨링
rem 종료: 모니터 창에서 q 또는 Esc
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PY=.venv\Scripts\python.exe

echo.
echo [1/2] 출발 전 점검...
%PY% tools\preflight.py
if errorlevel 1 (
  echo.
  echo 점검에서 실패 항목이 있습니다. 그래도 시작하려면 아무 키나 누르세요. 중단: Ctrl+C
  pause >nul
)

echo.
echo [2/2] 시연 시작 - 신호등 상태를 말하면 학습 데이터로 저장됩니다 (빨간불, 초록불, 좌회전, 맞아, 취소 ...)
%PY% -m csd --show --fullscreen --label-voice --name demo
echo.
echo 종료되었습니다. 녹화와 라벨은 recordings 폴더에 있습니다.
pause
