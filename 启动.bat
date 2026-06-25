@echo off
chcp 65001 >nul
echo.
echo  ╔══════════════════════════════════════════╗
echo  ║   词云环 v4.1 — 三阶段管道启动中...     ║
echo  ╚══════════════════════════════════════════╝
echo.
cd /d "%~dp0"
start http://localhost:5000
python app.py
pause
