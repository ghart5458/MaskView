@echo off
cd /d "%~dp0"
uv run python main.py
if %ERRORLEVEL% neq 0 (
    echo.
    echo MaskView exited with error code %ERRORLEVEL%
    pause
)
