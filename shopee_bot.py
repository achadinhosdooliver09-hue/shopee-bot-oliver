import os
import sys
import time
import json
import html
import hashlib
import logging
import threading
from collections import deque
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler

# ─── UTF-8 no console ────────────────────────────────────────────────────────
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ─── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("shopee_bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("ShopeeDealBot")

# ─── Health Check HTTP (Render / UptimeRobot) ────────────────────────────────
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"OK - Achadinhos do Oliver Bot Active")

    def log_message(self, format, *args):
        pass


def start_http_server():
    port = int(os.environ.get("PORT", 10000))
    try:
        server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
        logger.info(f"✅ Servidor HTTP de Health Check ativo na porta {port}")
        server.serve_forever()
    except Exception as e:
        logger.error(f"❌ Erro no servidor HTTP: {e}")


# ─── Config ──────────────────────────────────────────────────────────────────
CONFIG_PATH = "config.json"


def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Erro ao ler config.json: {e}")
    return {}


# ─── Shopee Affiliate API ─────────────────────────────────────────────────────
class ShopeeAffiliateAPI:
    """Cliente para a API GraphQL de Afiliados Shopee."""

    def __init__(self, app_key, secret_key):
        self.app_key = str(app_key).strip()
        self.secret_key = str(secret_key).strip()
        self.endpoint = "https://open-api.affiliate.shopee.com.br/graphql"
        self.headers_base = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

    def _generate_signature(self, timestamp, payload):
        factor = f"{self.app_key}{timestamp}{payload}{self.secret_key}"
        return hashlib.sha256(factor.encode("utf-8")).hexdigest()

    def _headers(self, timestamp, payload):
        sig = self._generate_signature(timestamp, payload)
        headers = self.headers_base.copy()
        headers.update({
            "Content-Type": "application/json",
            "Authorization": f"SHA256 Credential={self.app_key}, Timestamp={timestamp}, Signature={sig}",
        })
        return headers

    def fetch_top_offers(self, page=1, limit=10):
        if not self.app_key or not self.secret_key:
            return []
        timestamp = int(time.time())
        query = (
            "query { productOfferV2(page: %d, limit: %d) "
            "{ nodes { itemId productName price offerLink imageUrl commissionRate } } }"
            % (page, limit)
        )
        payload = json.dumps({"query": query}, separators=(",", ":"))
        try:
            res = requests.post(
                self.endpoint,
                data=payload,
                headers=self._headers(timestamp, payload),
                timeout=25,
            )
            if res.status_code == 200:
                data = res.json()
                nodes = data.get("data", {}).get("productOfferV2", {}).get("nodes", [])
                if not nodes:
                    logger.warning(f"Shopee retornou lista vazia. Resposta: {data}")
                return nodes
            else:
                logger.error(f"Shopee API status {res.status_code}: {res.text[:300]}")
        except Exception as e:
            logger.error(f"Shopee API erro: {e}")
        return []

    def generate_affiliate_link(self, origin_url):
        if not origin_url:
            return origin_url
        timestamp = int(time.time())
        query = 'mutation { generateShortLink(input: { originUrl: "%s" }) { shortLink } }' % origin_url
        payload = json.dumps({"query": query}, separators=(",", ":"))
        try:
            res = requests.post(
                self.endpoint,
                data=payload,
                headers=self._headers(timestamp, payload),
                timeout=15,
            )
            if res.status_code == 200:
                short = res.json().get("data", {}).get("generateShortLink", {}).get("shortLink")
                if short:
                    return short
        except Exception as e:
            logger.error(f"Erro ao gerar link de afiliado: {e}")
        return origin_url


# ─── Telegram ────────────────────────────────────────────────────────────────
def send_telegram_deal(bot_token, chat_id, title, price, raw_offer_link, image_url, shopee_api, fallback_chat_id="@achadinhosdooliver"):
    if not bot_token or not chat_id:
        logger.warning("Telegram: bot_token ou chat_id ausentes")
        return False

    # Garante URL completa da imagem
    if image_url.startswith("//"):
        image_url = "https:" + image_url

    affiliate_link = shopee_api.generate_affiliate_link(raw_offer_link)

    try:
        price_fmt = f"R$ {float(price):.2f}"
    except (ValueError, TypeError):
        price_fmt = f"R$ {price}"

    # ESCAPE DE HTML para evitar erro 400 do Telegram
    title_escaped = html.escape(title)
    affiliate_link_escaped = html.escape(affiliate_link, quote=True)

    caption = (
        f"🔥 <b>ACHADINHO IMPERDÍVEL NA SHOPEE!</b>\n\n"
        f"📦 <b>{title_escaped}</b>\n\n"
        f"💰 <b>Preço de Oferta:</b> {price_fmt}\n\n"
        f"👉 <a href=\"{affiliate_link_escaped}\"><b>CLIQUE AQUI PARA COMPRAR NA SHOPEE</b></a>\n\n"
        f"⚡ <i>Garanta o seu com desconto antes que acabe!</i>"
    )

    reply_markup = {
        "inline_keyboard": [
            [{"text": "🛒 COMPRAR COM DESCONTO NA SHOPEE", "url": affiliate_link}]
        ]
    }

    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    # Tenta o ID primario (ex: -1004304433240) e o fallback (@achadinhosdooliver) se necessario
    target_chats = [chat_id]
    if fallback_chat_id and fallback_chat_id != chat_id:
        target_chats.append(fallback_chat_id)

    for target in target_chats:
        payload = {
            "chat_id": target,
            "photo": image_url,
            "caption": caption,
            "parse_mode": "HTML",
            "reply_markup": json.dumps(reply_markup),
        }

        for attempt in range(1, 4):
            try:
                res = requests.post(url, data=payload, headers=headers, timeout=30)
                if res.status_code == 200:
                    logger.info(f"✅ Publicado no Telegram ({target}): {title[:35]}... | {affiliate_link}")
                    return True
                else:
                    resp_json = res.json()
                    desc = resp_json.get("description", res.text[:200])
                    err_code = resp_json.get("error_code")
                    logger.error(
                        f"Telegram erro destino '{target}' (tentativa {attempt}): "
                        f"code={res.status_code} desc={desc}"
                    )
                    if err_code in (400, 403, 404):
                        break
            except Exception as e:
                logger.error(f"Telegram erro inesperado (tentativa {attempt}) — {e}")
            time.sleep(2)

    return False


# ─── Main Loop ───────────────────────────────────────────────────────────────
def main():
    logger.info("🚀 Iniciando Robô Varredor Shopee 24/7 — Achadinhos do Oliver")

    http_thread = threading.Thread(target=start_http_server, name="HTTPServer", daemon=True)
    http_thread.start()

    cfg = load_config()

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN") or cfg.get("telegram_bot_token", "")
    chat_id   = os.environ.get("TELEGRAM_CHAT_ID") or cfg.get("telegram_chat_id", "")
    app_key   = os.environ.get("SHOPEE_APP_KEY") or cfg.get("shopee_app_key", "")
    secret    = os.environ.get("SHOPEE_SECRET_KEY") or cfg.get("shopee_secret_key", "")
    interval  = int(os.environ.get("CHECK_INTERVAL_MINUTES") or cfg.get("check_interval_minutes", 3)) * 60

    logger.info(f"📋 Config Ativa: chat_id={chat_id} | app_key={app_key[:6]}... | interval={interval//60}min")

    if not app_key or not secret or not bot_token or not chat_id:
        logger.error("❌ Credenciais incompletas! Encerrando com erro.")
        sys.exit(1)

    shopee_api = ShopeeAffiliateAPI(app_key, secret)

    # Fila circular de 200 itens para permitir reciclagem e nao travar no set infinito
    posted_queue = deque(maxlen=200)
    posted_items = set()

    page = 1

    while True:
        try:
            logger.info(f"🔎 Varrendo Shopee — página {page}...")
            offers = shopee_api.fetch_top_offers(page=page, limit=10)

            if offers:
                count = 0
                for item in offers:
                    item_id = item.get("itemId")
                    if not item_id or item_id in posted_items:
                        continue

                    title      = item.get("productName", "Produto Shopee")
                    price      = item.get("price", "0.00")
                    offer_link = item.get("offerLink", "")
                    image      = item.get("imageUrl", "")

                    if not offer_link or not image:
                        continue

                    if send_telegram_deal(bot_token, chat_id, title, price, offer_link, image, shopee_api):
                        posted_queue.append(item_id)
                        posted_items = set(posted_queue)
                        count += 1
                        time.sleep(5)  # Delay para respeitar rate limit do Telegram

                logger.info(f"✨ {count} novas ofertas enviadas nesta rodada")
                page = (page % 5) + 1
            else:
                logger.warning("⚠️ Nenhuma oferta retornada da Shopee nesta rodada")
                page = 1

        except Exception as e:
            logger.error(f"Erro no ciclo principal: {e}", exc_info=True)

        logger.info(f"💤 Aguardando {interval // 60} minutos para a próxima varredura...")
        time.sleep(interval)


if __name__ == "__main__":
    main()
