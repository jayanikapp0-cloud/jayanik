FROM python:3.12-slim

WORKDIR /app

# Copy application files
COPY otp_server.py core.py store.py ./
COPY web/ ./web/
COPY payments/ ./payments/

# لا تنسخ .env داخل الصورة — القيم تُمرَّر كمتغيرات بيئة من لوحة الاستضافة
# (Render/Docker run -e) بدلاً من كتابتها بالكود، حفاظاً على الأسرار
# (SESSION_SECRET وغيره) خارج صورة Docker والمستودع العام.

# Create data directory
RUN mkdir -p /app/data

EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/health')" || exit 1

CMD ["python3", "otp_server.py"]
