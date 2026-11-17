@echo off
chcp 65001 >nul
cd /d "%~dp0"
python logger.py --estimate
echo 노트북 절전을 끄고 이 창을 2주 동안 열어 두세요. 끄려면 Ctrl+C
python logger.py
pause
