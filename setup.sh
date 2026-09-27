#!/bin/bash
# تركيب جاينك على سيرفر لينكس — أمر واحد.
#
#   sudo bash setup.sh
#
# يضع الملفات في /opt/jaynak، ينشئ خدمة تعمل تلقائياً وتنجو من إعادة
# التشغيل، ويولّد سرّ الجلسات مرّة واحدة.
set -e
[ "$(id -u)" = "0" ] || { echo "شغّله بـ sudo"; exit 1; }

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST=/opt/jaynak

command -v python3 >/dev/null || { echo "→ تثبيت بايثون…"; apt-get update -qq && apt-get install -y -qq python3; }

id -u jaynak >/dev/null 2>&1 || useradd -r -s /usr/sbin/nologin -d "$DEST" jaynak
mkdir -p "$DEST/data"
cp -r "$SRC"/otp_server.py "$SRC"/core.py "$SRC"/store.py "$SRC"/web "$SRC"/payments "$DEST/"
[ -d "$SRC/tests" ] && cp -r "$SRC/tests" "$DEST/"

# سرّ الجلسات يُولَّد مرّة ويثبت: تغيّره يُبطل كل الجلسات **ويُخفي
# المستندات المرفوعة** لأن مسار مجلداتها مشتقّ منه.
if [ ! -f "$DEST/.env" ]; then
  if [ -f "$SRC/.env" ]; then
    cp "$SRC/.env" "$DEST/.env"
    echo "  ✓ تم نسخ ملف .env المخصص"
  else
    {
      echo "SESSION_SECRET=$(head -c32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
      echo "OWNER_PHONE=98061051"
      echo "ALLOW_DEV_CODE=1"
    } > "$DEST/.env"
    echo "  ✓ وُلّد SESSION_SECRET جديد"
  fi
fi
chmod 600 "$DEST/.env"
chown -R jaynak:jaynak "$DEST"

cat > /etc/systemd/system/jaynak.service <<'UNIT'
[Unit]
Description=Jaynak server
After=network.target

[Service]
Type=simple
User=jaynak
WorkingDirectory=/opt/jaynak
EnvironmentFile=-/opt/jaynak/.env
Environment=PORT=8787
Environment=DATA_FILE=/opt/jaynak/data/jaynak-data.json
Environment=SITE_DIR=/opt/jaynak/data/site-data
Environment=DOCS_DIR=/opt/jaynak/data/documents
ExecStart=/usr/bin/python3 /opt/jaynak/otp_server.py
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/jaynak/data

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now jaynak
sleep 2
systemctl is-active jaynak >/dev/null && echo "  ✓ الخدمة تعمل" || { journalctl -u jaynak -n 20 --no-pager; exit 1; }
curl -fsS http://127.0.0.1:8787/health && echo

command -v ufw >/dev/null && ufw allow 8787/tcp >/dev/null 2>&1 || true

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo
echo "════════════════════════════════════════"
echo "  جاهز:  http://${IP:-<عنوان-السيرفر>}:8787"
echo "════════════════════════════════════════"
echo
echo "  ⚠ هذا http بلا تشفير. لا ترفع بطاقات هوية حقيقية قبل"
echo "    إضافة https — يحتاج اسم نطاق. راجع README-AR.md"
