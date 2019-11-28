from selenium import webdriver
from bs4 import BeautifulSoup
import configparser
import json
import time
import requests
import copy
import logging
import gspread
import boto3
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timedelta

logger = logging.getLogger()

config = configparser.ConfigParser()
config.read("./config.ini")

BUCKET_NAME = config.get("company", "bucket_name")

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

yesterday = datetime.strftime(datetime.now() - timedelta(1), '%Y/%m/%d')

# AM7:00に回る場合は昨日レポートも取得
yesterday_flag = False
dt_now = datetime.now()
if int(dt_now.hour) == 7 and int(dt_now.minute) < 30:
  yesterday_flag = True

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

    # 各メディア毎に処理
    for media, media_name in MEDIAS.items():
        report = eval(media)(driver)

        # 当日分をSLACK通知
        post_slack("*"+media_name+"* " + add_pre_format(json.dumps(report[TODAY], indent=2, ensure_ascii=False)))

        # 昨日分をspredsheetに送る
        if yesterday_flag:
            for yesterday_report in report[YESTERDAY]:
                post_spreadsheet(yesterday_report, media)

    driver.close()
    driver.quit()

# スマートニュース
def sn(driver):
    MEDIA = "sn"
    LOGIN_URL = "https://partners.smartnews-ads.com/login"
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

    # s3に置いてる稼働中キャンペーンIDを取得する
    s3 = boto3.client('s3')
    file_name = f'active_campaigns/{MEDIA}.csv'
    response = s3.get_object(Bucket=BUCKET_NAME, Key=file_name)
    body = response['Body'].read().decode('utf-8')
    campaign_ids = body.split(',')

    # レポートデータ取得
    report_hash = eval(f'{MEDIA}_get_spending_data')(driver, campaign_ids, MEDIA)

    # 審査中ステータス取得
    report_hash[TODAY].extend(eval(f'{MEDIA}_get_ad_status')(driver, campaign_ids, MEDIA))

    return report_hash

def squad(driver):
    MEDIA = "squad"
    LOGIN_URL = "https://squad-affiliate.com/"
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

    # レポートデータ取得
    report_hash = eval(f'{MEDIA}_get_reward_data')(driver, MEDIA)

    return report_hash

def sn_get_spending_data(driver, campaign_ids, media):
    GOAL_URL  = "https://partners.smartnews-ads.com/manager/account/campaigns/%s"
    report_hash = copy.deepcopy(REPORT_HASH)

    # 日付毎に回す
    for date in SELECT_DAYS:

        # 昨日レポートは指定時間しか取得しない
        if date == YESTERDAY and yesterday_flag == False:
          continue

        # キャンペーンごとに回してレポート取得
        for campaign_id in campaign_ids:

            # 目的のページへ遷移
            driver.get(GOAL_URL % str(campaign_id))

            time.sleep(1)

            driver.find_element_by_id('insights-datepicker').click()
            driver.find_element_by_xpath("//li[@data-range-key='"+SELECT_DAYS[date]+"']").click()

            time.sleep(1)

            # レポートページをパース
            report_hash[date].extend(eval(f'{media}_parse_report')(driver))

        # 合計spending計算
        total_spending = 0
        for report in report_hash[date]:
            total_spending += convert_str_to_int_money(report['SPENDING'])

        report_hash[date].append({TOTAL_SPENDING : "{:,}".format(total_spending)+"円"})

    return report_hash

def sn_parse_report(driver):
    COLUMNS = {
        'NAME':0,
        'DAILY_BUDGET':7,
        'SPENDING':11,
        'VCTR':15,
#        'CVR':16,
        'CPA':17,
        'CPC':18,
#        'CPM':19,
#        'IMP':20,
#        'CTR':21,
    }

    html = driver.page_source.encode('utf-8')
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select('.fixedDataTableLayout_rowsContainer .fixedDataTableRowLayout_rowWrapper')

    report_array = []
    for i, row in enumerate(rows):
        if i == 0:
            continue
        span = row.select('.public_fixedDataTableCell_cellContent span')
        my_name = config.get("company", "my")
        if my_name not in span[COLUMNS['NAME']].get_text():
            continue
        if span[COLUMNS['SPENDING']].get_text() == "-":
            continue

        columns_dict = {}
        for k, v in COLUMNS.items():
            columns_dict[k] = span[v].get_text()

        report_array.append(columns_dict)

    return report_array

def sn_get_ad_status(driver, campaign_ids, media):
    GOAL_URL  = "https://partners.smartnews-ads.com/advertiser/%s/campaign"
    ad_status_array = []

    # キャンペーンごとに回してレポート取得
    for campaign_id in campaign_ids:

        # 目的のページへ遷移
        driver.get(GOAL_URL % str(campaign_id))

        time.sleep(1)

        # レポートページをパース
        ad_status_array.append(eval(f'{media}_parse_status')(driver))

    return ad_status_array

def sn_parse_status(driver):
    html = driver.page_source.encode('utf-8')
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select('.editable-over.ng-scope')

    status_dict = {}
    for i, row in enumerate(rows):
        td = row.find('td', sortable="'name'")
        a = td.find('a')
        campaign_name = a.get_text()
        my_name = config.get("company", "my")
        if my_name not in campaign_name:
            continue

        warning = row.select_one('.badge.badge-warning')
        reviewing = 0
        if warning:
            reviewing = warning.select_one('.ng-binding').get_text()

        status_dict[campaign_name] = "審査中：" + str(reviewing)
    return status_dict

def squad_get_reward_data(driver, media):
    GOAL_URL = "https://squad-affiliate.com/affiliaters/275/reports"
    report_hash = copy.deepcopy(REPORT_HASH)

    # 日付毎に回す
    for date in SELECT_DAYS:

        # 昨日レポートは指定時間しか取得しない
        if date == YESTERDAY and yesterday_flag == False:
          continue

        # 目的ページに遷移
        driver.get(GOAL_URL)
        driver.find_element_by_xpath("//input[@data-disable-with='"+SELECT_DAYS[date]+"']").click()

        time.sleep(1)

        # レポートページをパース
        report_hash[date].extend(eval(f'{media}_parse_report')(driver))

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

def squad_parse_report(driver):
    COLUMNS = {
        'NAME':2,
        'MEDIA':3,
        'CV':4,
        'REWARD':5,
    }

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
    value_input_option = 'USER_ENTERED'
    spending = 'spending'
    reward = 'reward'

    if TOTAL_SPENDING in report:
        sheet.append_row(
            [
                yesterday,
                media,
                spending,
                '{}_{}_{}'.format(yesterday, media, spending),
                convert_str_to_int_money(report[TOTAL_SPENDING]),
            ],
            value_input_option
        )
    if TOTAL_REWARD in report:
        for key in MEDIAS:
            if key != SQUAD_VERIFY:
                sheet.append_row(
                    [
                        yesterday,
                        key,
                        reward,
                        '{}_{}_{}'.format(yesterday, key, reward),
                        convert_str_to_int_money(report[MEDIAS_SQUAD_CONVERT[key]+TOTAL_REWARD]),
                    ],
                    value_input_option
                )


def add_pre_format(message):
    return "```{}```".format(message)


def convert_str_to_int_money(money_str):
    return int(money_str.replace('¥', '').replace(',', '').replace('円',''))
