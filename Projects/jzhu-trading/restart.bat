@echo off
echo Restarting JZhu Trading...
echo.

:: Pre-flight: path must only contain safe characters.
:: Uses PowerShell because batch pipes break on Chinese GBK encoding.
powershell -NoProfile -Command "$p=(Get-Location).Path; if($p -match ' '){exit 2}; if($p -match '[^a-zA-Z0-9:\\._ -]'){exit 3}; exit 0" >nul 2>nul
if errorlevel 3 goto :BAD_PATH_NONASCII
if errorlevel 2 goto :BAD_PATH_SPACE

set "HOST_INSTALL_PATH=%CD%"

:: LAN IP detection. Skips 127.* / 169.254.*.
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do (
    if not defined HOST_LAN_IP (
        for /f "tokens=*" %%b in ("%%a") do (
            echo %%b| findstr /r "^127\. ^169\.254\." >nul
            if errorlevel 1 set "HOST_LAN_IP=%%b"
        )
    )
)

docker info >nul 2>&1
if errorlevel 1 goto :NO_DOCKER

docker compose down 2>nul

:: If another Docker container (not ours) still holds our ports, stop it.
setlocal enabledelayedexpansion
set "OUR_PROJECT=deploy"
for %%i in ("%CD%") do set "OUR_PROJECT=%%~ni"

for %%p in (3000 8180) do (
    for /f %%c in ('docker ps --filter publish=%%p -q 2^>nul') do (
        for /f "tokens=*" %%x in ('docker inspect --format "{{index .Config.Labels \"com.docker.compose.project\"}}" %%c 2^>nul') do (
            if /i not "%%x"=="!OUR_PROJECT!" (
                for /f "tokens=*" %%n in ('docker inspect --format "{{.Name}}" %%c 2^>nul') do (
                    echo Port %%p is in use by Docker container %%n ^(project: %%x^)
                    echo Stopping it...
                )
                docker stop %%c >nul 2>&1
            )
        )
    )
)
endlocal

echo Pulling latest and starting...
docker compose pull --quiet
docker compose up -d
echo.
echo ============================================
echo   Ready! Opening browser...
echo   http://localhost:3000
echo ============================================
timeout /t 3 >nul
start http://localhost:3000
goto :EOF

:NO_DOCKER
echo ============================================
echo.
echo   Docker Desktop is not running.
echo.
echo   Please install and start Docker Desktop:
echo   https://docker.com/products/docker-desktop
echo.
echo   Choose the right version:
echo     Mac M1/M2/M3/M4  = Apple Silicon
echo     Mac Intel         = Intel Chip
echo     Windows           = AMD64
echo.
echo   Free to use. Skip login when prompted.
echo   Then run restart.bat again.
echo.
echo ============================================
pause
goto :EOF

:BAD_PATH_SPACE
echo ============================================
echo.
echo   [ERROR] Install path contains a SPACE.
echo.
echo   Current path:
echo   %CD%
echo.
echo   Docker cannot reliably handle spaces in
echo   bind-mount paths. The database container
echo   will fail to load.
echo.
echo   FIX: Move the jzhu-trading folder to a
echo   path with NO SPACES and re-run.
echo.
echo   Recommended: C:\jzhu-trading\
echo.
echo ============================================
pause
exit /b 1

:BAD_PATH_NONASCII
echo ============================================
echo.
echo   [ERROR] Path contains non-English characters.
echo.
echo   Folder names in the install path must only
echo   contain: a-z, A-Z, 0-9, dash, underscore.
echo.
echo   Chinese / Japanese / special characters will
echo   cause the Docker database to fail on startup.
echo.
echo   FIX: Move jzhu-trading to a pure English path.
echo   Recommended: C:\jzhu-trading\
echo.
echo ============================================
pause
exit /b 1
