@echo off
setlocal

set "NUNIF_DIR=%~dp0.."

@rem source checkout with a venv
if exist "%NUNIF_DIR%\venv\Scripts\pythonw.exe" (
  pushd "%NUNIF_DIR%" && start "" "%NUNIF_DIR%\venv\Scripts\pythonw.exe" -m iw3_ext.gui && popd
  exit /b 0
)

@rem windows package layout: setenv.bat sits next to the nunif directory
if exist "%NUNIF_DIR%\..\setenv.bat" (
  call "%NUNIF_DIR%\..\setenv.bat"
  pushd "%NUNIF_DIR%" && start "" pythonw -m iw3_ext.gui && popd
  exit /b 0
)

@rem last resort: whatever python is on PATH
pushd "%NUNIF_DIR%" && start "" pythonw -m iw3_ext.gui && popd
exit /b 0
