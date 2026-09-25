@echo off
rem ===== 바탕화면에 "운전 상황 모니터" 바로가기 만들기 =====
rem   운전 상황 모니터            : 앱만 열기 (시작 버튼을 눌러 시작)
rem   운전 상황 모니터 (자동 시작) : 열자마자 엔진 시작 (차량 시연용)
chcp 65001 >nul
cd /d "%~dp0"
if not exist publish\CarSignalDetector.exe (
  echo publish\CarSignalDetector.exe 가 없습니다. 먼저 build.bat 을 실행하세요.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s = New-Object -ComObject WScript.Shell; $d = [Environment]::GetFolderPath('Desktop'); $exe = (Resolve-Path 'publish\CarSignalDetector.exe').Path;" ^
  "$a = $s.CreateShortcut((Join-Path $d '운전 상황 모니터.lnk')); $a.TargetPath = $exe; $a.WorkingDirectory = (Split-Path $exe); $a.Save();" ^
  "$b = $s.CreateShortcut((Join-Path $d '운전 상황 모니터 (자동 시작).lnk')); $b.TargetPath = $exe; $b.Arguments = '--autostart'; $b.WorkingDirectory = (Split-Path $exe); $b.Save();" ^
  "Write-Host '바탕화면에 바로가기 2개를 만들었습니다.'"
pause
