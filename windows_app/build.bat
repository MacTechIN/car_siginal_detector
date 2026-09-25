@echo off
rem ===== Windows 앱 빌드 → windows_app\publish\CarSignalDetector.exe =====
rem 필요: .NET 8 SDK (빌드), .NET 8 데스크톱 런타임 (실행)
chcp 65001 >nul
cd /d "%~dp0"
dotnet publish CsdApp\CsdApp.csproj -c Release -r win-x64 --self-contained false ^
  -p:PublishSingleFile=true -p:IncludeNativeLibrariesForSelfExtract=true -o publish
if errorlevel 1 (
  echo 빌드 실패
  pause
  exit /b 1
)
echo.
echo 완료: %~dp0publish\CarSignalDetector.exe
pause
