@echo off
set "CLAUDECODE="
set "PATH=C:\Program Files\nodejs;%APPDATA%\npm;%PATH%"
set "TASK=%~1"
set "DIR=%~2"
set "WORKER_NUM=%~3"
set "ROLE=%~4"
if "%DIR%"=="" set "DIR=F:\Claude App"
if "%WORKER_NUM%"=="" set "WORKER_NUM=?"
if "%ROLE%"=="" set "ROLE=worker"

:: Per-worker instance file to avoid conflicts
if "%ROLE%"=="human" (
    set "CLAMBAKE_INSTANCE_FILE=%USERPROFILE%\.clambake_instance_human"
    title Human Shell
) else (
    set "CLAMBAKE_INSTANCE_FILE=%USERPROFILE%\.clambake_instance_worker_%WORKER_NUM%"
    title Worker %WORKER_NUM%
)

:: Set role env var so clambake up picks it up
set "CLAMBAKE_ROLE=%ROLE%"

cd /d "%DIR%"

if "%ROLE%"=="human" (
    :: Human mode: register with clambake, then drop to interactive bash
    python F:\Docker\clambake\clambake.py up --role human
    echo.
    echo === Human Shell ===
    echo   Registered with Clambake as human. Instance file: %CLAMBAKE_INSTANCE_FILE%
    echo   Type 'exit' to close. Instance will go stale after 30min without heartbeat.
    echo.
    "C:\Program Files\Git\bin\bash.exe" -i
) else (
    :: Worker mode: launch claude with the task
    "C:\Program Files\Git\bin\bash.exe" -i -c "claude --dangerously-skip-permissions \"%TASK%\""
)
