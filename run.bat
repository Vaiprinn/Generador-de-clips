@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title ClipForge

REM ---- 1. Python 3.11 ---------------------------------------------------------
set "PY="
where py >nul 2>nul && (py -3.11 -c "import sys" >nul 2>nul && set "PY=py -3.11")
if not defined PY (
    where python >nul 2>nul && (python -c "import sys; sys.exit(0 if sys.version_info[:2]>=(3,10) else 1)" >nul 2>nul && set "PY=python")
)
if not defined PY (
    echo [ClipForge] No encuentro Python 3.11. Instalalo desde https://www.python.org/downloads/
    echo             o con:  winget install Python.Python.3.11
    pause
    exit /b 1
)

REM ---- 2. Entorno virtual -----------------------------------------------------
if not exist ".venv\Scripts\python.exe" (
    echo [ClipForge] Creando entorno virtual .venv ...
    %PY% -m venv .venv || (echo Error creando el entorno & pause & exit /b 1)
)
set "VPY=.venv\Scripts\python.exe"

REM ---- 3. Dependencias (solo si cambian los requirements) ----------------------
set "STAMP=.venv\.deps_ok"
set "NEED_INSTALL=0"
if not exist "%STAMP%" set "NEED_INSTALL=1"
for %%F in (requirements.txt requirements-gpu.txt) do (
    if exist "%STAMP%" (
        for /f %%R in ('powershell -NoProfile -Command "if ((Get-Item '%%F').LastWriteTime -gt (Get-Item '%STAMP%').LastWriteTime) {1} else {0}"') do if "%%R"=="1" set "NEED_INSTALL=1"
    )
)
if "%NEED_INSTALL%"=="1" (
    echo [ClipForge] Instalando dependencias ...
    "%VPY%" -m pip install --upgrade pip
    "%VPY%" -m pip install -r requirements.txt || (echo Error instalando dependencias & pause & exit /b 1)
    where nvidia-smi >nul 2>nul && (
        echo [ClipForge] GPU NVIDIA detectada: instalando librerias CUDA para Whisper ...
        "%VPY%" -m pip install -r requirements-gpu.txt
    )
    echo ok> "%STAMP%"
)

REM ---- 4. ffmpeg.exe + fuentes -------------------------------------------------
"%VPY%" tools\setup_assets.py

REM ---- 4b. Motor opcional Remotion (necesita Node.js) ------------------------
where node >nul 2>nul
if errorlevel 1 (
    echo [ClipForge] Motor Remotion: no encuentro Node.js. Es opcional; para usarlo:
    echo             winget install OpenJS.NodeJS.LTS   y vuelve a abrir run.bat
) else (
    set "NEED_NPM=0"
    if not exist "remotion\node_modules\.clipforge_ok" set "NEED_NPM=1"
    if exist "remotion\node_modules\.clipforge_ok" (
        for /f %%R in ('powershell -NoProfile -Command "if ((Get-Item 'remotion\package.json').LastWriteTime -gt (Get-Item 'remotion\node_modules\.clipforge_ok').LastWriteTime) {1} else {0}"') do if "%%R"=="1" set "NEED_NPM=1"
    )
    call :remotion_install
)

REM ---- 5. Lanzar (abre el navegador en http://127.0.0.1:7860) ------------------
if not exist ".env" if exist ".env.example" copy ".env.example" ".env" >nul
echo [ClipForge] Abriendo http://127.0.0.1:7860  (cierra esta ventana para salir)
"%VPY%" app.py
pause

goto :eof

:remotion_install
if "%NEED_NPM%"=="0" goto :eof
echo [ClipForge] Instalando el motor Remotion (una sola vez) ...
pushd remotion
call npm ci --no-audit --no-fund || call npm install --no-audit --no-fund
if not errorlevel 1 (
    call node node_modules\@remotion\cli\remotion-cli.js browser ensure
    echo ok> node_modules\.clipforge_ok
)
popd
goto :eof
