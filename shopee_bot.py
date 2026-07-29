import os
import sys
import time
import json
import hashlib
import logging
import threading
import requests
from flask import Flask

# Servidor HTTP para passar no Health Check do Render Web Service
app = Flask(__name__)

@app.route('/')
@app.route('/health')
def health_check():
    return "OK - Shopee Bot Active 24/7", 200

def start_flask():
    port = int(os.environ.get("PORT", 10000))
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    app.run(host="0.0.0.0", port=port)

# Garante codificação UTF-8 no console
sys.stdout.reconfigure(encoding='utf-8')

# Configuração de Logs
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("shopee_bot.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("ShopeeDealBot")

CONFIG_PATH = "config.json"

def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Erro ao ler config.json: {e}")
    return {}

class ShopeeAffiliateAPI:
    """Cliente Oficial para a API GraphQL de Afiliados Shopee."""
    def __init__(self, app_key, secret_key):
        self.app_key = str(app_key).strip()
        self.secret_key = str(secret_key).strip()
        self.endpoint = "https://open-api.affiliate.shopee.com.br/graphql"

    def _generate_signature(self, timestamp, payload):
        factor = f"{self.app_key}{timestamp}{payload}{self.secret_key}"
        return hashlib.sha256(factor.encode('utf-8')).hexdigest()

    def fetch_top_offers(self, page=1, limit=10):
        """Busca as melhores ofertas da Shopee."""
        if not self.app_key or not self.secret_key:
            return []

        timestamp = int(time.time())
        query = 'query { productOfferV2(page: %d, limit: %d) { nodes { itemId productName price offerLink imageUrl commissionRate } } }' % (page, limit)
        payload = json.dumps({'query': query}, separators=(',', ':'))
        signature = self._generate_signature(timestamp, payload)

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"SHA256 Credential={self.app_key}, Timestamp={timestamp}, Signature={signature}"
        }

        try:
            res = requests.post(self.endpoint, data=payload, headers=headers, timeout=15)
            if res.status_code == 200:
                data = res.json()
                nodes = data.get("data", {}).get("productOfferV2", {}).get("nodes", [])
                return nodes
            else:
                logger.error(f"Erro Shopee API Status {res.status_code}: {res.text}")
        except Exception as e:
            logger.error(f"Falha ao conectar com Shopee API: {e}")
        return []

    def generate_affiliate_link(self, origin_url):
        """Gera o link de afiliado oficial encurtado (s.shopee.com.br)."""
        if not self.app_key or not self.secret_key or not origin_url:
            return origin_url

        timestamp = int(time.time())
        query = 'mutation { generateShortLink(input: { originUrl: "%s" }) { shortLink } }' % origin_url
        payload = json.dumps({'query': query}, separators=(',', ':'))
        signature = self._generate_signature(timestamp, payload)

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"SHA256 Credential={self.app_key}, Timestamp={timestamp}, Signature={signature}"
        }

        try:
            res = requests.post(self.endpoint, data=payload, headers=headers, timeout=12)
            if res.status_code == 200:
                data = res.json()
                short_link = data.get("data", {}).get("generateShortLink", {}).get("shortLink")
                if short_link:
                    return short_link
        except Exception as e:
            logger.error(f"Erro ao gerar link de afiliado: {e}")

        return origin_url

def send_telegram_deal(bot_token, chat_id, title, price, raw_offer_link, image_url, shopee_api):
    """Gera o link de afiliado e envia o post de oferta para o Telegram."""
    if not bot_token or not chat_id:
        logger.warning("Telegram Bot Token ou Chat ID não fornecidos.")
        return False

    # 1. Converte o link do produto para o seu link de afiliado oficial
    affiliate_link = shopee_api.generate_affiliate_link(raw_offer_link)

    # 2. Formata a legenda em HTML
    caption = (
        f"🔥 <b>ACHADINHO IMPERDÍVEL NA SHOPEE!</b>\n\n"
        f"📦 <b>{title}</b>\n\n"
        f"💰 <b>Preço de Oferta:</b> R$ {float(price):.2f}\n\n"
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
        "reply_markup": json.dumps(reply_markup)
    }

    # Tenta enviar com retentativas
    for attempt in range(1, 4):
        try:
            res = requests.post(url, data=payload, timeout=25)
            if res.status_code == 200:
                logger.info(f"✅ Oferta publicada no Telegram: {title[:35]} | Link: {affiliate_link}")
                return True
            else:
                logger.error(f"Erro ao postar no Telegram (Tentativa {attempt}): {res.text}")
        except Exception as e:
            logger.error(f"Falha de conexão Telegram (Tentativa {attempt}): {e}")
        time.sleep(2)
        
    return False

def main():
    # Inicia o servidor HTTP Flask em segundo plano para o Render Web Service
    http_thread = threading.Thread(target=start_flask, daemon=True)
    http_thread.start()

    logger.info("🚀 Iniciando Robô Varredor Shopee 24/7 (Achadinhos do Oliver)...")

    cfg = load_config()
    bot_token = cfg.get("telegram_bot_token") or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id   = cfg.get("telegram_chat_id")   or os.environ.get("TELEGRAM_CHAT_ID")
    app_key   = cfg.get("shopee_app_key")     or os.environ.get("SHOPEE_APP_KEY")
    secret    = cfg.get("shopee_secret_key")  or os.environ.get("SHOPEE_SECRET_KEY")
    interval  = int(cfg.get("check_interval_minutes", 15)) * 60

    if not app_key or not secret:
        logger.error("❌ AppKey ou SecretKey da Shopee não foram configurados!")
        return

    shopee_api = ShopeeAffiliateAPI(app_key, secret)
    posted_items = set()

    page = 1
    while True:
        try:
            logger.info(f"🔎 Varrendo produtos em oferta na Shopee (Página {page})...")
            offers = shopee_api.fetch_top_offers(page=page, limit=10)

            if offers:
                count = 0
                for item in offers:
                    item_id = item.get("itemId")
                    if item_id in posted_items:
                        continue

                    title = item.get("productName", "Produto Shopee")
                    price = item.get("price", "0.00")
                    offer_link = item.get("offerLink", "")
                    image = item.get("imageUrl", "")

                    if send_telegram_deal(bot_token, chat_id, title, price, offer_link, image, shopee_api):
                        posted_items.add(item_id)
                        count += 1
                        time.sleep(4) # Intervalo entre mensagens para evitar bloqueio no Telegram

                logger.info(f"✨ {count} novas ofertas da Shopee publicadas no Telegram.")
                page = page + 1 if page < 5 else 1
            else:
                logger.warning("Nenhuma oferta retornada da API Shopee nesta rodada.")

        except Exception as e:
            logger.error(f"Erro no ciclo principal: {e}")

        logger.info(f"💤 Aguardando {interval // 60} minutos para a próxima varredura de achadinhos...")
        time.sleep(interval)

if __name__ == "__main__":
    main()
