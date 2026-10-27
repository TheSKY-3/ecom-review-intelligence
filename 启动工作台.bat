@echo off
rem Start the local-mode review workbench on http://127.0.0.1:8765
rem Keep this window open while using the workbench; closing it stops the server.
cd /d "%~dp0"
echo ============================================
echo  Workbench: http://127.0.0.1:8765
echo  Keep this window OPEN. Close it to stop.
echo ============================================
".venv\Scripts\python.exe" scripts\run_phase7_workbench.py --port 8765 --mode local --project-root . --acknowledge-local-data
echo.
echo Server stopped.
pause
