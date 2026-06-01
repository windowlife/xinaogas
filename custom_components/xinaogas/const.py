from __future__ import annotations

DOMAIN = "xinaogas"

CONF_TOKEN = "token"
CONF_CITY_NAME = "city_name"
CONF_CITY_ID = "city_id"
CONF_UPDATE_INTERVAL = "update_interval"
CONF_CARDBIND_ID = "cardbind_id"
CONF_PLATFORM_ONLY_CARD_NO = "platform_only_card_no"

DEFAULT_CITY_NAME = "长沙"
DEFAULT_CITY_ID = "62"
DEFAULT_UPDATE_INTERVAL_MINUTES = 720

APPKEY_SECRET = "8796135e9f8349d998345f9f13d8bd95"

BIND_CARDS_URL = "https://lp.ecej.com/v2/homepage/ios/getFamilyAllBindCardsWithNfc.json"
BALANCE_URL = "https://lp.ecej.com/v2/homepage/getCardBalanceV2.json"
BILL_URL = "https://lp.ecej.com/v1/module/ordinary/queryHisDetailBill.json"

USER_AGENT = "ECEJ/6.6.2 (com.ecej.ECEJ; build:101387; iOS 26.4.2) Alamofire/4.9.1"
