@echo off
rem Builds dist\StudyHub\StudyHub.exe (needs: pip install pyinstaller).
rem Afterwards run installer\Install.bat to install / update it.
setlocal
cd /d "%~dp0"
set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=python"

echo [1/3] Icon...
"%PY%" installer\make_icon.py || goto :fail

echo [2/3] PyInstaller...
"%PY%" -m PyInstaller StudyHub.spec --noconfirm --clean || goto :fail

echo [3/3] Copying installer scripts...
copy /y installer\Install.bat dist\StudyHub\ >nul
copy /y installer\install.ps1 dist\StudyHub\ >nul
copy /y installer\uninstall.ps1 dist\StudyHub\ >nul
rem one icon per app + the list of Start-menu shortcuts (install.ps1 creates them)
if not exist dist\StudyHub\icons mkdir dist\StudyHub\icons
copy /y installer\icons\*.ico dist\StudyHub\icons\ >nul
copy /y installer\apps.json dist\StudyHub\ >nul
rem Phone app (built separately in ..\StudyHubAndroid); the phone downloads it from the Phone page.
set "APK=%~dp0..\StudyHubAndroid\app\build\outputs\apk\release\app-release.apk"
if exist "%APK%" (
  if not exist dist\StudyHub\android mkdir dist\StudyHub\android
  copy /y "%APK%" dist\StudyHub\android\StudyHub.apk >nul
) else (
  echo   no phone APK found - skipped
)

echo.
echo Done: dist\StudyHub\StudyHub.exe
exit /b 0

:fail
echo Build failed.
exit /b 1
