#!/bin/bash
# ============================================================
# Mini App HTTPS 一键打通(交付版, 换 HOST 就能用)
#   用法: HOST=你的域名或IP.sslip.io bash setup_https.sh
#   说明: Telegram Mini App 必须 HTTPS + 有效证书; 没域名可以用 sslip.io 免费泛解析
#         (把 IP 里的点换成横线: YOUR_HOST → YOUR_HOST.sslip.io)
#   做完: 浏览器开 https://$HOST/ 能出页面, 再在 bot.py 启动时给管理员设菜单按钮。
# ============================================================
set -u
HOST="${HOST:-}"
APP_DIR="${APP_DIR:-/opt/deepseek-bot/miniapp}"
API_PORT="${API_PORT:-8901}"
LOG=/tmp/miniapp_setup.log
if [ -z "$HOST" ]; then echo "用法: HOST=xxx.sslip.io bash setup_https.sh"; exit 1; fi
: > "$LOG"; mkdir -p /var/www/certbot "$APP_DIR"

# ① certbot 装进独立 venv(系统 pip 里的 cryptography 常跟 apt 版打架)
CB=/opt/certbot/bin/certbot
if [ ! -x "$CB" ]; then
  python3 -m venv /opt/certbot >>"$LOG" 2>&1 || { export DEBIAN_FRONTEND=noninteractive; apt-get install -y python3-venv >>"$LOG" 2>&1; python3 -m venv /opt/certbot >>"$LOG" 2>&1; }
  /opt/certbot/bin/pip install --upgrade pip >>"$LOG" 2>&1
  /opt/certbot/bin/pip install certbot >>"$LOG" 2>&1
fi
[ -x "$CB" ] || { echo "certbot 装不上, 看 $LOG"; exit 1; }

# ② 80 口只留 ACME 校验 + 跳 https; 443 服务静态页 + /api 反代到 bot 进程
cat > /etc/nginx/conf.d/miniapp.conf <<NGINX
server {
    listen 80;
    listen [::]:80;
    server_name $HOST;
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { return 301 https://\$host\$request_uri; }
}
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name $HOST;
    ssl_certificate     /etc/letsencrypt/live/$HOST/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$HOST/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    root $APP_DIR;
    index index.html;
    client_max_body_size 200m;
    location /api/ {
        proxy_pass http://127.0.0.1:$API_PORT;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_read_timeout 600s;
        proxy_send_timeout 600s;
    }
    location / { try_files \$uri /index.html; }
}
NGINX
nginx -t >>"$LOG" 2>&1 || { echo "nginx 配置有问题, 看 $LOG"; exit 1; }

# ③ 先签证书再 reload(顺序反了 443 起不来)
if [ ! -f "/etc/letsencrypt/live/$HOST/fullchain.pem" ]; then
  cp /etc/nginx/conf.d/miniapp.conf /tmp/miniapp.conf.full
  python3 - <<'PY' > /etc/nginx/conf.d/miniapp.conf
import re
c=open('/tmp/miniapp.conf.full',encoding='utf-8').read()
i=c.find('server {\n    listen 443')
open('/dev/stdout','w',encoding='utf-8').write(c[:i])
PY
  systemctl reload nginx
  $CB certonly --webroot -w /var/www/certbot -d "$HOST" --non-interactive --agree-tos \
      --register-unsafely-without-email >>"$LOG" 2>&1
  cp /tmp/miniapp.conf.full /etc/nginx/conf.d/miniapp.conf
fi
systemctl reload nginx && echo "NGINX_OK"
[ -f "/etc/letsencrypt/live/$HOST/fullchain.pem" ] && echo "CERT_OK" || { echo "证书失败, 看 $LOG"; exit 1; }

# ④ bot 侧依赖(FastAPI 收文件需要)
/opt/deepseek-bot/.venv/bin/pip install -q python-multipart 2>>"$LOG" || pip3 install -q python-multipart 2>>"$LOG"
echo "完成 → https://$HOST/   (记得把 bot.py 里的 PUBLIC_URL 改成同一个域名)"
