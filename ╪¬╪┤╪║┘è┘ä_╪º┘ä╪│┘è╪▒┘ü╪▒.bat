@echo off
chcp 65001 >nul
title سيرفر جاينك - Jaynak Server
color 0B
cd /d "%~dp0"

echo ==========================================================
echo        جاينك — جاري تجهيز السيرفر...
echo ==========================================================
echo.

rem ---- تحقّق من وجود بايثون ----
where python >nul 2>&1
if errorlevel 1 (
    echo [X] بايثون غير مثبّت على هذا الجهاز.
    echo     نزّله من https://www.python.org/downloads ثم شغّل هذا الملف مرة ثانية.
    echo.
    pause
    exit /b 1
)

rem ---- تحميل إعدادات .env كمتغيرات بيئة (السيرفر لا يقرأها تلقائياً) ----
if exist ".env" (
    for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do (
        if not "%%A"=="" set "%%A=%%B"
    )
    echo [✓] تم تحميل الإعدادات من .env
) else (
    echo [!] لا يوجد ملف .env — سيعمل السيرفر بالإعدادات الافتراضية.
)

rem ---- تجهيز مجلد البيانات (data) الذي يشير إليه .env ----
if not exist "data" mkdir "data"
if not exist "data\site-data" mkdir "data\site-data"
if not exist "data\documents" mkdir "data\documents"

if not defined PORT set "PORT=8787"

rem نقل بيانات قديمة من المجلد الرئيسي إلى data\ إن وُجدت ولم تُنقل بعد
if exist "jaynak-data.json" if not exist "data\jaynak-data.json" (
    copy /y "jaynak-data.json" "data\jaynak-data.json" >nul
    echo [✓] تم نقل بيانات الطلبات والحسابات القديمة إلى data\
)

echo.
echo [✓] الخادم يعمل الآن:
echo     الرابط المحلي:   http://localhost:%PORT%
echo     لوحة التحكم:     http://localhost:%PORT%/
echo     فحص الحالة:      http://localhost:%PORT%/health
echo.
echo ==========================================================
echo اضغط Ctrl+C لإيقاف السيرفر في أي وقت.
echo ==========================================================
echo.

start http://localhost:%PORT%
python otp_server.py
pause
