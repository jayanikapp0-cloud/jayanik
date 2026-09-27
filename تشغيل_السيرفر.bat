@echo off
chcp 65001 >nul
title سيرفر جاينك - Jayanik Server
color 0B
echo ==========================================================
echo        جاينك — جاري تشغيل السيرفر...
echo ==========================================================
cd /d "%~dp0"
echo.
echo [✓] الخادم يعمل الآن:
echo     الرابط المحلي:   http://localhost:8787
echo     لوحة التحكم:     http://localhost:8787/
echo     فحص الحالة:     http://localhost:8787/health
echo.
echo ==========================================================
echo اضغط Ctrl+C لإيقاف السيرفر في أي وقت.
echo ==========================================================
echo.
start http://localhost:8787
python otp_server.py
pause