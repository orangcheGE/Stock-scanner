import streamlit as st
import requests
from bs4 import BeautifulSoup
import pandas as pd
import numpy as np
import time
import re
import io
import urllib.parse
from datetime import datetime

# (get_headers, get_market_sum_pages, get_price_data 함수는 이전과 동일)

def get_headers():
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://finance.naver.com/'
    }

def get_market_sum_pages(page_list, market="KOSPI"):
    sosok = 0 if market == "KOSPI" else 1
    codes, names, changes = [], [], []
    for page in page_list:
        url = f"https://finance.naver.com/sise/sise_market_sum.naver?sosok={sosok}&page={page}"
        try:
            res = requests.get(url, headers=get_headers())
            res.encoding = 'euc-kr'
            soup = BeautifulSoup(res.text, 'html.parser')
            table = soup.select_one('table.type_2')
            if not table: continue
            for tr in table.select('tr'):
                tds = tr.find_all('td')
                if len(tds) < 5: continue
                a = tr.find('a', href=True)
                if not a: continue
                match = re.search(r'code=(\d{6})', a['href'])
                if match:
                    codes.append(match.group(1))
                    names.append(a.get_text(strip=True))
                    changes.append(tds[4].get_text(strip=True))
            time.sleep(0.3)
        except: continue
    return pd.DataFrame({'종목코드': codes, '종목명': names, '등락률': changes})

def get_price_data(code, max_pages=30): # 데이터 충분히 가져오기
    url = f"https://finance.naver.com/item/sise_day.naver?code={code}"
    dfs = []
    for page in range(1, max_pages+1):
        try:
            res = requests.get(f"{url}&page={page}", headers=get_headers())
            df_list = pd.read_html(io.StringIO(res.text), encoding='euc-kr')
            if df_list: dfs.append(df_list[0])
        except: continue
    if not dfs: return pd.DataFrame()
    df = pd.concat(dfs, ignore_index=True).dropna(how='all')
    df = df.rename(columns=lambda x: x.strip())
    for col in ['종가','고가','저가','거래량']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col].astype(str).str.replace(',',''), errors='coerce')
    df['날짜'] = pd.to_datetime(df['날짜'], errors='coerce')
    return df.dropna(subset=['날짜','종가']).sort_values('날짜').reset_index(drop=True)


def analyze_stock(code, name, current_change):
    try:
        df_price = get_price_data(code, max_pages=20) # 데이터 충분히 확보
        # 일목균형표(52일)+미래(26일) 기간을 위해 최소 80일 이상 데이터 필요
        if df_price is None or len(df_price) < 80:
            return None

        # --- 1. 현재/과거 지표 계산 ---
        df_present = df_price.set_index('날짜')
        df_present['5MA'] = df_present['종가'].rolling(5).mean()
        df_present['20MA'] = df_present['종가'].rolling(20).mean()
        df_present['60MA'] = df_present['종가'].rolling(60).mean()
        high_9 = df_present['고가'].rolling(9).max()
        low_9 = df_present['저가'].rolling(9).min()
        df_present['tenkan_sen'] = (high_9 + low_9) / 2
        high_26 = df_present['고가'].rolling(26).max()
        low_26 = df_present['저가'].rolling(26).min()
        df_present['kijun_sen'] = (high_26 + low_26) / 2
        high_52 = df_present['고가'].rolling(52).max()
        low_52 = df_present['저가'].rolling(52).min()
        df_present['senkou_b_base'] = (high_52 + low_52) / 2
        ema12 = df_present['종가'].ewm(span=12, adjust=False).mean()
        ema26 = df_present['종가'].ewm(span=26, adjust=False).mean()
        df_present['MACD'] = ema12 - ema26
        df_present['MACD_Signal'] = df_present['MACD'].ewm(span=9, adjust=False).mean()
        df_present['MACD_hist'] = df_present['MACD'] - df_present['MACD_Signal']
        
        # --- 2. [핵심] 미래의 구름대를 계산하여 별도 DataFrame으로 생성 ---
        df_future = pd.DataFrame(index=df_present.index)
        df_future['senkou_a'] = (df_present['tenkan_sen'] + df_present['kijun_sen']) / 2
        df_future['senkou_b'] = df_present['senkou_b_base']
        # 인덱스를 26일 미래로 이동
        df_future.index = df_future.index + pd.DateOffset(days=26)
        
        # --- 3. 현재 데이터와 미래(구름대) 데이터를 날짜 기준으로 병합 ---
        df_merged = pd.merge(df_present, df_future, left_index=True, right_index=True, how='left')
        
        # 분석을 위해 최종 데이터 정제
        df_final = df_merged.dropna().copy()
        if len(df_final) < 4: return None

        # --- 4. '오늘'의 정확한 구름대 상태 분석 ---
        last = df_final.iloc[-1]
        prev = df_final.iloc[-2]
        
        price_today = last['종가']
        cloud_top_today = max(last['senkou_a'], last['senkou_b'])
        cloud_bottom_today = min(last['senkou_a'], last['senkou_b'])
        
        price_yesterday = prev['종가']
        cloud_top_yesterday = max(prev['senkou_a'], prev['senkou_b'])
        
        ichimoku_status = "🌫️ 구름대 진입"
        if price_today > cloud_top_today:
            if price_yesterday <= cloud_top_yesterday:
                ichimoku_status = "🔥 최근 상향돌파"
            else:
                ichimoku_status = "📈 구름대 위"
        elif price_today < cloud_bottom_today:
            if price_yesterday >= cloud_top_yesterday: # '구름대 위'에서 '아래'로 떨어진 경우도 '하향이탈'로 포함
                ichimoku_status = "🧊 최근 하향이탈"
            else:
                ichimoku_status = "📉 구름대 아래"
        
        # --- 5. [수정] MA 크로스오버 & MACD 신호 분석 ---
        def get_ma_crossover_status(last_row, prev_row, ma_col):
            price_last, ma_last = last_row['종가'], last_row[ma_col]
            price_prev, ma_prev = prev_row['종가'], prev_row[ma_col]

            # 골든크로스: 어제 가격이 MA와 같거나 아래였고, 오늘 가격이 MA 위로 올라섬
            is_golden_cross = (price_prev <= ma_prev) and (price_last > ma_last)
            # 데드크로스: 어제 가격이 MA와 같거나 위였고, 오늘 가격이 MA 아래로 내려감
            is_dead_cross = (price_prev >= ma_prev) and (price_last < ma_last)

            if is_golden_cross: return "🔥 골든크로스"
            if is_dead_cross: return "🧊 데드크로스"
            
            if price_last > ma_last: return "📈 상승 유지"
            else: return "📉 하락 유지"

        status_5ma = get_ma_crossover_status(last, prev, '5MA')
        status_20ma = get_ma_crossover_status(last, prev, '20MA')
        status_60ma = get_ma_crossover_status(last, prev, '60MA')
        ma_crossover_text = f"5MA: {status_5ma} | 20MA: {status_20ma} | 60MA: {status_60ma}"
        
        status = "관망"
        if (last['MACD_hist'] > 0 and prev['MACD_hist'] <= 0): status = "적극 매수"
        elif (last['MACD_hist'] < 0 and prev['MACD_hist'] >= 0): status = "적극 매도"
        elif ichimoku_status == '🔥 최근 상향돌파': status = "매수 관심"
        elif ichimoku_status == '🧊 최근 하향이탈': status = "매도 관심"
        elif last['종가'] > last['20MA']: status = "홀드"

        disparity = ((last['종가'] / last['20MA']) - 1) * 100 if last['20MA'] > 0 else 0
        disparity_fmt = f"{'+' if disparity >= 0 else ''}{round(disparity, 2)}%"
        chart_url = f"https://finance.naver.com/item/fchart.naver?code={code}"

        return [code, name, current_change, int(last['종가']), disparity_fmt, status, ichimoku_status, ma_crossover_text, chart_url]
    except Exception as e:
        return None


def show_styled_dataframe(dataframe):
    if dataframe.empty:
        st.write("분석된 데이터가 없습니다. 왼쪽에서 '분석 시작'을 눌러주세요.")
        return

    def style_ichimoku_column(val):
        if '🔥 최근 상향돌파' in val:
            return 'color: white; background-color: #c62828; font-weight: bold;'
        if '🧊 최근 하향이탈' in val:
            return 'color: white; background-color: #1565c0; font-weight: bold;'
        if '📈 구름대 위' in val: return 'color: #d32f2f;'
        if '📉 구름대
