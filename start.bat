@echo off
chcp 65001 >nul
echo 启动 基金分析 Agent ...
echo 浏览器访问：http://127.0.0.1:8000
echo 按 Ctrl+C 停止服务
python app_fastapi.py
pause
