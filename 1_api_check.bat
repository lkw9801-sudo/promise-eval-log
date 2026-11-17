@echo off
chcp 65001 >nul
cd /d "%~dp0"
python api_check.py --repeat 6
echo.
echo 결과: check_out\check_report.md  (이 파일을 Claude에게 보내 주세요)
pause
