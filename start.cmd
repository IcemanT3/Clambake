@echo off
title Clambake Orchestrator
echo =========================================
echo   CLAMBAKE ORCHESTRATOR - tmux Session
echo =========================================
echo.
echo Choose launch mode:
echo   1 = Planner only
echo   2 = 3 Coders (tasks #2, #3, #4)
echo   3 = All agents (planner + coder + qa + reviewer)
echo   4 = Reattach to existing tmux session
echo.
set /p MODE="Enter choice (1-4): "

if "%MODE%"=="1" (
    set ROLES=planner
    echo Launching planner agent...
) else if "%MODE%"=="2" (
    set ROLES=coder coder coder
    echo Launching 3 coder agents...
) else if "%MODE%"=="3" (
    set ROLES=planner coder qa reviewer
    echo Launching all agents...
) else if "%MODE%"=="4" (
    echo Reattaching to existing tmux session...
    docker exec -it clambake-orchestrator bash -c "export TMUX_TMPDIR=/tmp/clambake-tmux && tmux attach -t clambake"
    pause
    exit /b
) else (
    echo Invalid choice.
    pause
    exit /b
)

echo.
echo Once inside tmux:
echo   Ctrl+B then 0 = Dashboard
echo   Ctrl+B then 1,2,3... = Agent panes
echo   Ctrl+B then D = Detach (exit tmux)
echo.
docker exec -it clambake-orchestrator bash -c "export TMUX_TMPDIR=/tmp/clambake-tmux && export CLAMBAKE_ENABLED=1 && unset CLAUDECODE && bash /opt/clambake/launch-tmux.sh mindmeld %ROLES%"
pause
