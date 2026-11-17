@echo off
chcp 65001 >nul
cd /d "%~dp0"
python evaluate.py && python report.py && start out\report.html
pause
