from selenium import webdriver
from bs4 import BeautifulSoup
import configparser
import json
import time
import requests
import copy
import logging
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timedelta

logger = logging.getLogger()

SMART_NEWS_VERIFY = 'sn'
SQUAD_VERIFY = 'squad'

MEDIAS = {
    SMART_NEWS_VERIFY: 'スマートニュース',
    SQUAD_VERIFY: 'SQUADレポート',
}
MEDIAS_SQUAD_CONVERT = {
    SMART_NEWS_VERIFY: 'SmartNews',
    SQUAD_VERIFY: '',
}
TODAY = 'today'
YESTERDAY = 'yesterday'
SELECT_DAYS = {
    TODAY:'今日',
    YESTERDAY:'昨日',
}
REPORT_HASH = {
    TODAY: [],
    YESTERDAY: [],
}
TOTAL_SPENDING = '合計消費'
TOTAL_REWARD = '合計報酬'

config = configparser.ConfigParser()
config.read("./config.ini")

yesterday = datetime.strftime(datetime.now() - timedelta(1), '%Y-%m-%d')

def lambda_handler(event, context):
    options = webdriver.ChromeOptions()

    # のちほどダウンロードするバイナリを指定
    options.binary_location = "./bin/headless-chromium"

    # headlessで動かすために必要なオプション
    options.add_argument("--headless")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1280x1696")
    options.add_argument("--disable-application-cache")
    options.add_argument("--disable-infobars")
    options.add_argument("--no-sandbox")
    options.add_argument("--hide-scrollbars")
    options.add_argument("--enable-logging")
    options.add_argument("--log-level=0")
    options.add_argument("--single-process")
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--homedir=/tmp")

    driver = webdriver.Chrome(
        "./bin/chromedriver",
        chrome_options=options)

    # AM8:00に回る場合は昨日レポートも取得
    dt_now = datetime.now()
    yesterday_flag = False
    if int(dt_now.hour) == 8 and int(dt_now.minute) < 30:
      yesterday_flag = True

    # 各メディア毎に処理
    for media, media_name in MEDIAS.items():
        report = eval(media)(driver, yesterday_flag)

        # 当日分をSLACK通知
        post_slack("*"+media_name+"* " + add_pre_format(json.dumps(report[TODAY], indent=2, ensure_ascii=False)))

        # 昨日分をspredsheetに送る
        if yesterday_flag:
            for yesterday_report in report[YESTERDAY]:
                post_spreadsheet(yesterday_report, media)

    driver.close()
    driver.quit()

# スマートニュース
def sn(driver, yesterday_flag):
    MEDIA = "sn"
    CAMPAIGN_IDS = [19517007, 12993177]
    LOGIN_URL = "https://partners.smartnews-ads.com/login"
    GOAL_URL  = "https://partners.smartnews-ads.com/manager/account/campaigns/"
    ID_ELM_NAME = "loginId"
    PW_ELM_NAME = "password"
    LOGIN_BUTTON_ELM_NAME = "btn-login"

    driver.get(LOGIN_URL)

    # ID/PASSを取得
    id = config.get("id", f"{MEDIA}_id")
    password = config.get("pw", f"{MEDIA}_pw")

    # ID/PASSを入力
    driver.find_element_by_name(ID_ELM_NAME).send_keys(id)
    driver.find_element_by_name(PW_ELM_NAME).send_keys(password)

    # ログイン
    driver.find_element_by_class_name(LOGIN_BUTTON_ELM_NAME).click()

    report_hash = copy.deepcopy(REPORT_HASH)

    # 日付毎に回す
    for date in SELECT_DAYS:

        # 昨日レポートは指定時間しか取得しない
        if date == YESTERDAY and yesterday_flag == False:
          continue

        # キャンペーンごとに回してレポート取得
        for campaign_id in CAMPAIGN_IDS:

            # 目的のページへ遷移
            driver.get(GOAL_URL + str(campaign_id))

            time.sleep(1)

            driver.find_element_by_id('insights-datepicker').click()

            report_hash[date].extend(parse_smartnews_report(driver, date))

        # 合計spending計算
        total_spending = 0
        for report in report_hash[date]:
            total_spending += convert_str_to_int_money(report['SPENDING'])

        report_hash[date].append({TOTAL_SPENDING : "{:,}".format(total_spending)+"円"})
    return report_hash

def squad(driver, yesterday_flag):
    MEDIA = "squad"
    LOGIN_URL = "https://squad-affiliate.com/"
    GOAL_URL = "https://squad-affiliate.com/affiliaters/275/reports"
    ID_ELM_NAME = "affiliater[email]"
    PW_ELM_NAME = "affiliater[password]"
    LOGIN_BUTTON_ELM_NAME = "commit"

    driver.get(LOGIN_URL)

    # ID/PASSを取得
    id = config.get("id", f"{MEDIA}_id")
    password = config.get("pw", f"{MEDIA}_pw")

    # ID/PASSを入力
    driver.find_element_by_name(ID_ELM_NAME).send_keys(id)
    driver.find_element_by_name(PW_ELM_NAME).send_keys(password)

    # ログイン
    driver.find_element_by_name(LOGIN_BUTTON_ELM_NAME).click()

    report_hash = copy.deepcopy(REPORT_HASH)

    # 日付毎に回す
    for date in SELECT_DAYS:

        # 昨日レポートは指定時間しか取得しない
        if date == YESTERDAY and yesterday_flag == False:
          continue

        # 目的ページに遷移
        driver.get(GOAL_URL)

        report_hash[date].extend(parse_squad_report(driver, date))

        # 合計reward計算
        total_reward = {}
        total_reward[TOTAL_REWARD] = 0
        for report in report_hash[date]:
            total_reward[report['MEDIA']+TOTAL_REWARD]  = total_reward.get(report['MEDIA']+TOTAL_REWARD, 0)
            total_reward[report['MEDIA']+TOTAL_REWARD] += convert_str_to_int_money(report['REWARD'])
            total_reward[TOTAL_REWARD] += convert_str_to_int_money(report['REWARD'])

        for key, value in total_reward.items():
            total_reward[key] = "{:,}".format(value)+"円"

        report_hash[date].append(total_reward)

    return report_hash

def parse_smartnews_report(driver, target_day):
    COLUMNS = {
        'NAME':0,
        'DAILY_BUDGET':7,
        'SPENDING':9,
        'VCTR':13,
#        'CVR':14,
        'CPA':15,
        'CPC':16,
#        'CPM':17,
#        'IMP':18,
#        'CTR':19,
    }

    driver.find_element_by_xpath("//li[@data-range-key='"+SELECT_DAYS[target_day]+"']").click()

    time.sleep(1)

    html = driver.page_source.encode('utf-8')
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select('.fixedDataTableLayout_rowsContainer .fixedDataTableRowLayout_rowWrapper')

    report_array = []
    for i, row in enumerate(rows):
        if i == 0:
            continue
        span = row.select('.public_fixedDataTableCell_cellContent span')
        if span[COLUMNS['SPENDING']].get_text() == "-":
            continue

        columns_dict = {}
        for k, v in COLUMNS.items():
            columns_dict[k] = span[v].get_text()

        report_array.append(columns_dict)

    return report_array

def parse_squad_report(driver, target_day):
    COLUMNS = {
        'NAME':2,
        'MEDIA':3,
        'CV':4,
        'REWARD':5,
    }

    driver.find_element_by_xpath("//input[@data-disable-with='"+SELECT_DAYS[target_day]+"']").click()

    html = driver.page_source.encode('utf-8')
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select('.table-wrapper')
    rows = table[0].select("tr")

    report_array = []
    for i, row in enumerate(rows):
        if i == 0:
            continue
        td = row.select('td')

        columns_dict = {}
        for k, v in COLUMNS.items():
            columns_dict[k] = td[v].get_text()

        report_array.append(columns_dict)

    return report_array

def post_slack(post_message):
    webhook_url = config.get("slack", "webhook_url")
    payload = {
        "text": post_message,
        "username": "ADレポート",
        "icon_emoji": ':snake:',
    }

    requests.post(webhook_url, data=json.dumps(payload))

def post_spreadsheet(report, media):
    scope = ['https://spreadsheets.google.com/feeds',
             'https://www.googleapis.com/auth/drive']

    spread_id = config.get("google", "spread_id")
    credentials = ServiceAccountCredentials.from_json_keyfile_name('addailyreport-254511-3504e1145325.json', scope)
    gc = gspread.authorize(credentials)
    wb = gc.open_by_key(spread_id)
    sheet = wb.worksheet("rowdata")

    if TOTAL_SPENDING in report:
        sheet.append_row([yesterday, media, 'spending', convert_str_to_int_money(report[TOTAL_SPENDING])])
    if TOTAL_REWARD in report:
        for key in MEDIAS:
            if key != SQUAD_VERIFY:
                sheet.append_row([yesterday, key, 'reward', convert_str_to_int_money(report[MEDIAS_SQUAD_CONVERT[key]+TOTAL_REWARD])])


def add_pre_format(message):
    return "```{}```".format(message)


def convert_str_to_int_money(money_str):
    return int(money_str.replace('¥', '').replace(',', '').replace('円',''))
