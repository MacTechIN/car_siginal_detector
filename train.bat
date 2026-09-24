@echo off
rem ===== 음성 라벨로 모은 신호등 데이터로 분류기 학습 =====
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PY=.venv\Scripts\python.exe

echo [1/2] 데이터셋 만들기 (recordings\*\crops -> dataset\tl_cls)
%PY% tools\build_tl_dataset.py
if errorlevel 1 goto :end

echo.
echo [2/2] 학습 (CPU, 수 분) -> models\tl_cls.pt  (다음 실행부터 자동 사용)
%PY% tools\train_tl.py

:end
pause
