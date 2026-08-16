#!/bin/bash
# Установка раздельного VPN-туннеля на коробке Albery. Запускать НА СЕРВЕРЕ из /var/www/albery.
#
# Правка маршрутов — единственное изменение, которое способно отрезать саму возможность его
# починить. Поэтому порядок такой: снимок → отложенный автооткат → применение → проверка.
# Если проверка не пройдена или связь оборвалась, откат сработает сам через ROLLBACK_MIN минут
# и вернёт прежнюю таблицу маршрутов.
#
#   ./scripts/deploy_vpn_split_tunnel.sh --probe     # только замер, ничего не меняет
#   ./scripts/deploy_vpn_split_tunnel.sh --apply     # установка с автооткатом
#   ./scripts/deploy_vpn_split_tunnel.sh --confirm   # отменить автооткат (после проверки)
#   ./scripts/deploy_vpn_split_tunnel.sh --rollback  # откатить немедленно
set -u

REPO=${ALBERY_REPO:-/var/www/albery}
IFACE=${VPN_IFACE:-awg0}
TABLE=${VPN_ROUTING_TABLE:-200}
ROLLBACK_MIN=${VPN_ROLLBACK_MINUTES:-12}
SNAPSHOT_DIR=${VPN_SNAPSHOT_DIR:-/root/vpn-split-rollback}
BACKUP_DIR="/root/albery-backups-$(date +%Y%m%d-%H%M%S)/vpn"

say() { printf '%s\n' "$*"; }

snapshot() {
  mkdir -p "$SNAPSHOT_DIR"
  ip rule show > "$SNAPSHOT_DIR/rules.txt"
  ip route show table "$TABLE" > "$SNAPSHOT_DIR/table.txt"
  say "снимок сохранён: $SNAPSHOT_DIR (правил: $(wc -l < "$SNAPSHOT_DIR/rules.txt"), маршрутов: $(wc -l < "$SNAPSHOT_DIR/table.txt"))"
}

do_rollback() {
  [ -s "$SNAPSHOT_DIR/table.txt" ] || { say "нет снимка — откатывать не к чему"; return 1; }
  ip route flush table "$TABLE" 2>/dev/null
  while IFS= read -r line; do
    [ -z "$line" ] && continue
    # shellcheck disable=SC2086
    ip route add $line table "$TABLE" 2>/dev/null
  done < "$SNAPSHOT_DIR/table.txt"
  say "таблица $TABLE восстановлена из снимка"
}

case "${1:---help}" in
  --probe)
    exec "$REPO/scripts/vpn_direct_probe.sh"
    ;;

  --rollback)
    do_rollback
    systemctl stop vpn-split-rollback.timer 2>/dev/null
    systemctl reset-failed vpn-split-rollback.service 2>/dev/null
    exit 0
    ;;

  --confirm)
    systemctl stop vpn-split-rollback.timer 2>/dev/null
    systemctl reset-failed vpn-split-rollback.service 2>/dev/null
    say "автооткат отменён — раздельный туннель принят"
    exit 0
    ;;

  --apply)
    :
    ;;

  *)
    sed -n '1,16p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
    ;;
esac

# --- 1. Замер до правки: он и есть обоснование состава allowlist -------------------------
say "=== замер прямой доступности (до правки) ==="
"$REPO/scripts/vpn_direct_probe.sh" | tee "$SNAPSHOT_DIR-probe.txt" 2>/dev/null || \
  "$REPO/scripts/vpn_direct_probe.sh"
say ""

# --- 2. Резервные копии всего, что заменяем ----------------------------------------------
mkdir -p "$BACKUP_DIR"
for f in /usr/local/sbin/vpn-watchdog.sh /usr/local/sbin/vpn-healthcheck.sh \
         /usr/local/sbin/vpn-apply.sh /root/vpn_apply.sh \
         /etc/albery/vpn-split-domains.conf; do
  [ -f "$f" ] && cp -a "$f" "$BACKUP_DIR/" 2>/dev/null
done
say "бэкап прежних скриптов: $BACKUP_DIR"

snapshot

# --- 3. Автооткат ДО применения ----------------------------------------------------------
systemctl stop vpn-split-rollback.timer 2>/dev/null
systemctl reset-failed vpn-split-rollback.service 2>/dev/null
systemd-run --on-active="${ROLLBACK_MIN}min" --unit=vpn-split-rollback \
  "$REPO/scripts/deploy_vpn_split_tunnel.sh" --rollback >/dev/null 2>&1 \
  && say "автооткат вооружён: сработает через ${ROLLBACK_MIN} мин, если не подтвердить" \
  || { say "НЕ УДАЛОСЬ вооружить автооткат — установка прервана"; exit 1; }

# --- 4. Установка файлов -----------------------------------------------------------------
install -m 0755 "$REPO/deploy/vpn-apply.sh"        /usr/local/sbin/vpn-apply.sh
install -m 0755 "$REPO/deploy/vpn-split-routes.sh" /usr/local/sbin/vpn-split-routes.sh
install -m 0755 "$REPO/deploy/vpn-watchdog.sh"     /usr/local/sbin/vpn-watchdog.sh
install -m 0755 "$REPO/deploy/vpn-healthcheck.sh"  /usr/local/sbin/vpn-healthcheck.sh
mkdir -p /etc/albery
# Конфиг НЕ перезатираем: на коробке он мог быть уточнён по замеру.
if [ ! -f /etc/albery/vpn-split-domains.conf ]; then
  install -m 0644 "$REPO/deploy/vpn-split-domains.conf" /etc/albery/vpn-split-domains.conf
  say "allowlist установлен из репозитория"
else
  say "allowlist уже существует — оставлен как есть (правьте /etc/albery/vpn-split-domains.conf)"
fi
install -m 0644 "$REPO/deploy/vpn-split-routes.service" /etc/systemd/system/vpn-split-routes.service
install -m 0644 "$REPO/deploy/vpn-split-routes.timer"   /etc/systemd/system/vpn-split-routes.timer
systemctl daemon-reload
systemctl enable --now vpn-split-routes.timer >/dev/null 2>&1

# Сторож обязан звать новый скрипт политики, а не прежний /root/vpn_apply.sh.
mkdir -p /etc/systemd/system/vpn-watchdog.service.d
cat > /etc/systemd/system/vpn-watchdog.service.d/split-tunnel.conf <<'UNIT'
[Service]
Environment=VPN_APPLY_SCRIPT=/usr/local/sbin/vpn-apply.sh
Environment=VPN_SPLIT_ROUTES=/usr/local/sbin/vpn-split-routes.sh
UNIT
systemctl daemon-reload

# --- 5. Применяем политику ---------------------------------------------------------------
say ""
say "=== применяем раздельный туннель ==="
if ! /usr/local/sbin/vpn-apply.sh; then
  say "vpn-apply завершился ошибкой — откатываюсь немедленно"
  do_rollback
  exit 1
fi
ip route show table "$TABLE" | sed 's/^/  /'

# --- 6. Проверка: продукт обязан пережить падение туннеля --------------------------------
say ""
say "=== проверка ==="
bitrix=$(curl -s -o /dev/null -m 15 -w '%{http_code}' https://b24-0xrp3s.bitrix24.ru/ || echo 000)
openai=$(curl -s -o /dev/null -m 20 -w '%{http_code}' https://api.openai.com/v1/models || echo 000)
tg=$(curl -s -o /dev/null -m 15 -w '%{http_code}' https://api.telegram.org || echo 000)
say "  Битрикс (должен быть НЕ 000): $bitrix"
say "  OpenAI  (должен быть 401):    $openai"
say "  Telegram (должен быть НЕ 000): $tg"
say ""
/usr/local/sbin/vpn-healthcheck.sh || true

say ""
if [ "$bitrix" != "000" ] && [ "$openai" = "401" ]; then
  say "ИТОГ: похоже на успех. Подтвердите вручную, иначе через ${ROLLBACK_MIN} мин будет откат:"
  say "  $REPO/scripts/deploy_vpn_split_tunnel.sh --confirm"
else
  say "ИТОГ: проверка НЕ пройдена. Откатить сейчас:"
  say "  $REPO/scripts/deploy_vpn_split_tunnel.sh --rollback"
fi
