from __future__ import annotations

DOMAIN = "xinaogas"

CONF_TOKEN = "token"
CONF_UPDATE_INTERVAL = "update_interval"
CONF_CONTRACT_NO = "contract_no"
CONF_PAYMENT_NO = "payment_no"
CONF_PLATFORM_CARD_NO = "platform_card_no"

DEFAULT_UPDATE_INTERVAL_MINUTES = 360
DATA_TOKEN_MANAGER = "token_manager"
DATA_COORDINATORS = "coordinators"

APPKEY_SALT = "8796135e9f8349d998345f9f13d8bd95"
BILL_SIGN_SALT = "n9yQq03q$BSfg1ao"

BASE_URL = "https://wechatapp.ecej.com"
PAYMENT_URL = f"{BASE_URL}/livingpay"

TOKEN_APPLY_URL = f"{BASE_URL}/businesshall/wechatapp/token/apply"
BIND_CARDS_URL = f"{PAYMENT_URL}/v3/xcx/getBingCardListV2.json"
BILL_URL = f"{PAYMENT_URL}/v3/xcx/getbill.json"
BILL_LIST_URL = f"{PAYMENT_URL}/v3/xcx/getBillListV2.json"
METER_GAS_URL = f"{PAYMENT_URL}/v3/xcx/iot/meterGasInfo.json"
ENERGY_ANALYSIS_URL = f"{PAYMENT_URL}/v3/xcx/electricity/getEnergyAnalysis.json"
ENERGY_ANALYSIS_URL2 = "https://lp.ecej.com/v3/xcx/electricity/getEnergyAnalysis.json"

USER_AGENT = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 MicroMessenger MiniProgram"
