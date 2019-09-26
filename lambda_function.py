from selenium import webdriver
from bs4 import BeautifulSoup
import configparser
import json
import time
import requests


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

    post_slack("*スマートニュース* " + add_pre_format(smart_news(driver)))
    post_slack("*SQUADレポート* " + add_pre_format(squad(driver)))

    driver.close()
    driver.quit()

def smart_news(driver):
    MEDIA = "sn"
    CAMPAIGN_ID = "19517007"
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
    LOGIN_URL = "https://partners.smartnews-ads.com/login"
    GOAL_URL = f"https://partners.smartnews-ads.com/manager/account/campaigns/{CAMPAIGN_ID}"
    ID_ELM_NAME = "loginId"
    PW_ELM_NAME = "password"
    LOGIN_BUTTON_ELM_NAME = "btn-login"

    driver.get(LOGIN_URL)

    # ID/PASSを取得
    config = configparser.ConfigParser()
    config.read("./config.ini")
    id = config.get("id", f"{MEDIA}_id")
    password = config.get("pw", f"{MEDIA}_pw")

    # ID/PASSを入力
    driver.find_element_by_name(ID_ELM_NAME).send_keys(id)
    driver.find_element_by_name(PW_ELM_NAME).send_keys(password)

    # ログイン 
    driver.find_element_by_class_name(LOGIN_BUTTON_ELM_NAME).click()

    # 目的ページに遷移
    driver.get(GOAL_URL)

    driver.find_element_by_id('insights-datepicker').click()

    driver.find_element_by_xpath("//li[@data-range-key='今日']").click()

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

    total_spending = 0
    for report in report_array:
        total_spending += convert_str_to_int_money(report['SPENDING'])

    report_array.append({'合計消費' : "{:,}".format(total_spending)+"円"})

    return json.dumps(report_array, indent=2, ensure_ascii=False) 

def squad(driver):
    MEDIA = "squad"
    COLUMNS = {
        'NAME':2,
        'MEDIA':3,
        'CV':4,
        'REWARD':5,
    }
    LOGIN_URL = "https://squad-affiliate.com/"
    GOAL_URL = "https://squad-affiliate.com/affiliaters/275/reports"
    ID_ELM_NAME = "affiliater[email]"
    PW_ELM_NAME = "affiliater[password]"
    LOGIN_BUTTON_ELM_NAME = "commit"

    driver.get(LOGIN_URL)

    # ID/PASSを取得
    config = configparser.ConfigParser()
    config.read("./config.ini")
    id = config.get("id", f"{MEDIA}_id")
    password = config.get("pw", f"{MEDIA}_pw")

    # ID/PASSを入力
    driver.find_element_by_name(ID_ELM_NAME).send_keys(id)
    driver.find_element_by_name(PW_ELM_NAME).send_keys(password)

    # ログイン 
    driver.find_element_by_name(LOGIN_BUTTON_ELM_NAME).click()

    # 目的ページに遷移
    driver.get(GOAL_URL)

    driver.find_element_by_xpath("//input[@data-disable-with='今日']").click()

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

    total_reward = 0
    for report in report_array:
        total_reward += convert_str_to_int_money(report['REWARD'])

    report_array.append({'合計報酬' : "{:,}".format(total_reward)+"円"})

    return json.dumps(report_array, indent=2, ensure_ascii=False) 

def post_slack(post_message):
    SLACK_WEBHOOK = "https://hooks.slack.com/services/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"
    payload = {
        "text": post_message,
        "username": "ADレポート",
        "icon_emoji": ':snake:',
    }

    requests.post(SLACK_WEBHOOK, data=json.dumps(payload))

def add_pre_format(message):
    return "```{}```".format(message) 


def convert_str_to_int_money(money_str):
    return int(money_str.replace('¥', '').replace(',', '').replace('円',''))
