import os
import sys
import time
import json
import hashlib
import logging
import threading
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler

# ─── UTF-8 no console (antes de qualquer log) ────────────────────────────────
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

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
        pass  # Silencia logs HTTP ruidosos


def start_http_server():
    """Inicia o servidor HTTP de health check. Roda em thread separada (não-daemon)."""
    port = int(os.environ.get("PORT", 10000))
    try:
        server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
        logger.info(f"✅ Servidor HTTP ativo na porta {port}")
        server.serve_forever()
    except OSError as e:
        logger.error(f"❌ ERRO CRÍTICO ao iniciar servidor HTTP na porta {port}: {e}")
    except Exception as e:
        logger.error(f"❌ Servidor HTTP encerrado inesperadamente: {e}")


# Thread NÃO-daemon: mantém o processo vivo mesmo se main() terminar
http_thread = threading.Thread(target=start_http_server, name="HTTPServer", daemon=False)
http_thread.start()
# Aguarda 1s para garantir que a porta está aberta antes do Render checar
time.sleep(1)

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

    def _generate_signature(self, timestamp, payload):
        factor = f"{self.app_key}{timestamp}{payload}{self.secret_key}"
        return hashlib.sha256(factor.encode("utf-8")).hexdigest()

    def _headers(self, timestamp, payload):
        sig = self._generate_signature(timestamp, payload)
        return {
            "Content-Type": "application/json",
            "Authorization": f"SHA256 Credential={self.app_key}, Timestamp={timestamp}, Signature={sig}",
        }

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
                timeout=20,
            )
            if res.status_code == 200:
                data = res.json()
                nodes = data.get("data", {}).get("productOfferV2", {}).get("nodes", [])
                if not nodes:
                    logger.warning(f"Shopee retornou lista vazia. Resposta: {data}")
                return nodes
            else:
                logger.error(f"Shopee API status {res.status_code}: {res.text[:300]}")
        except requests.exceptions.Timeout:
            logger.error("Shopee API: timeout na requisição")
        except Exception as e:
            logger.error(f"Shopee API: erro inesperado — {e}")
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
                logger.warning(f"generateShortLink sem retorno: {res.text[:200]}")
        except requests.exceptions.Timeout:
            logger.error("Timeout ao gerar link de afiliado")
        except Exception as e:
            logger.error(f"Erro ao gerar link de afiliado: {e}")
        return origin_url


# ─── Telegram ────────────────────────────────────────────────────────────────
def send_telegram_deal(bot_token, chat_id, title, price, raw_offer_link, image_url, shopee_api):
    if not bot_token or not chat_id:
        logger.warning("Telegram: bot_token ou chat_id ausentes")
        return False

    affiliate_link = shopee_api.generate_affiliate_link(raw_offer_link)

    try:
        price_fmt = f"R$ {float(price):.2f}"
    except (ValueError, TypeError):
        price_fmt = f"R$ {price}"

    caption = (
        f"🔥 <b>ACHADINHO IMPERDÍVEL NA SHOPEE!</b>\n\n"
        f"📦 <b>{title}</b>\n\n"
        f"💰 <b>Preço de Oferta:</b> {price_fmt}\n\n"
        f"👉 <a href='{affiliate_link}'><b>CLIQUE AQUI PARA COMPRAR NA SHOPEE</b></a>\n\n"
        f"⚡ <i>Garanta o seu com desconto antes que acabe!</i>"
    )

    reply_markup = {
        "inline_keyboard": [
            [{"text": "🛒 COMPRAR COM DESCONTO NA SHOPEE", "url": affiliate_link}]
        ]
    }

    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    payload = {
        "chat_id": chat_id,
        "photo": image_url,
        "caption": caption,
        "parse_mode": "HTML",
        "reply_markup": json.dumps(reply_markup),
    }

    for attempt in range(1, 4):
        try:
            res = requests.post(url, data=payload, timeout=30)
            if res.status_code == 200:
                logger.info(f"✅ Publicado: {title[:40]} | {affiliate_link}")
                return True
            else:
                resp_json = res.json()
                logger.error(
                    f"Telegram erro (tentativa {attempt}): "
                    f"code={res.status_code} desc={resp_json.get('description', res.text[:200])}"
                )
                # Se for erro de chat inválido, não tenta de novo
                if resp_json.get("error_code") in (400, 403):
                    logger.error("❌ Erro permanente Telegram — abortando tentativas")
                    break
        except requests.exceptions.Timeout:
            logger.error(f"Telegram: timeout (tentativa {attempt})")
        except Exception as e:
            logger.error(f"Telegram: erro inesperado (tentativa {attempt}) — {e}")
        time.sleep(3)

    return False


# ─── Main Loop ───────────────────────────────────────────────────────────────
def main():
    logger.info("🚀 Iniciando Robô Varredor Shopee 24/7 — Achadinhos do Oliver")

    cfg = load_config()
    bot_token = cfg.get("telegram_bot_token") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id   = cfg.get("telegram_chat_id")   or os.environ.get("TELEGRAM_CHAT_ID", "")
    app_key   = cfg.get("shopee_app_key")     or os.environ.get("SHOPEE_APP_KEY", "")
    secret    = cfg.get("shopee_secret_key")  or os.environ.get("SHOPEE_SECRET_KEY", "")
    interval  = int(cfg.get("check_interval_minutes", 15)) * 60

    # Log das configs (sem expor segredos)
    logger.info(f"📋 Config: chat_id={chat_id} | app_key={app_key[:6]}... | interval={interval//60}min")

    if not app_key or not secret:
        logger.error("❌ SHOPEE_APP_KEY ou SHOPEE_SECRET_KEY não configurados! Encerrando.")
        return

    if not bot_token or not chat_id:
        logger.error("❌ TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID não configurados! Encerrando.")
        return

    shopee_api = ShopeeAffiliateAPI(app_key, secret)
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
                        logger.warning(f"Item {item_id} sem link/imagem, pulando")
                        continue

                    if send_telegram_deal(bot_token, chat_id, title, price, offer_link, image, shopee_api):
                        posted_items.add(item_id)
                        count += 1
                        time.sleep(5)  # Respeita rate limit do Telegram

                logger.info(f"✨ {count} novas ofertas publicadas nesta rodada")
                page = (page % 5) + 1
            else:
                logger.warning("⚠️ Nenhuma oferta retornada da Shopee nesta rodada")
                page = 1  # Reseta página em caso de falha

        except Exception as e:
            logger.error(f"Erro no ciclo principal: {e}", exc_info=True)

        logger.info(f"💤 Aguardando {interval // 60} minutos para a próxima varredura...")
        time.sleep(interval)


if __name__ == "__main__":
    main()
