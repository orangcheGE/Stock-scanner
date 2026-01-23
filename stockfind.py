import streamlit as st
import requests
from bs4 import BeautifulSoup
import pandas as pd
import numpy as np
import time
import re
import io
from datetime import datetime

# 페이지 설정
st.set_page_config(page_title="실전 20일선 스캐너", layout="wide")

# -------------------------
# 필수 함수 정의부
# -------------------------
def get_headers():
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://finance.naver.com/'
    }

def get_market_sum_pages(pages, market="KOSPI"):
    sosok = 0 if market == "KOSPI" else 1
    codes, names, changes = [], [], []
    for page in pages:
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
                    span = tds[4].find('span')
                    changes.append(span.get_text(strip=True) if span else '0')
            time.sleep(1.5)
        except: continue
    return pd.DataFrame({'종목코드': codes, '종목명': names, '등락률(%)': changes})

def get_price_data(code, max_pages=15):
    url = f"https://finance.naver.com/item/sise_day.naver?code={code}"
    dfs = []
    for page in range(1, max_pages+1):
        pg_url = f"{url}&page={page}"
        try:
            res = requests.get(pg_url, headers=get_headers())
            df_list = pd.read_html(io.StringIO(res.text), encoding='euc-kr')
            if df_list: dfs.append(df_list[0])
        except: continue
        time.sleep(np.random.uniform(0.3, 0.5))
    if not dfs: return pd.DataFrame()
    df = pd.concat(dfs, ignore_index=True).dropna(how='all')
    df = df.rename(columns=lambda x: x.strip())
    for col in ['종가','시가','고가','저가','거래량']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col].astype(str).str.replace(',',''), errors='coerce')
    df['날짜'] = pd.to_datetime(df['날짜'], errors='coerce')
    return df.dropna(subset=['날짜','종가']).sort_values('날짜').reset_index(drop=True)

def analyze_stock(code, name, atr_multiplier_sl=2.0):
    try:
        df = get_price_data(code)
        if df is None or len(df) < 40: return None

        # 지표 계산
        df['20MA'] = df['종가'].rolling(20).mean()
        df['vol_ma5'] = df['거래량'].rolling(5).mean()
        ema12 = df['종가'].ewm(span=12, adjust=False).mean()
        ema26 = df['종가'].ewm(span=26, adjust=False).mean()
        df['MACD_hist'] = (ema12 - ema26) - (ema12 - ema26).ewm(span=9, adjust=False).mean()
        
        # ATR 계산
        df['tr'] = np.maximum(df['고가'] - df['저가'], 
                              np.maximum(abs(df['고가'] - df['종가'].shift(1)), 
                                         abs(df['저가'] - df['종가'].shift(1))))
        df['ATR'] = df['tr'].rolling(14).mean()

        last, prev = df.iloc[-1], df.iloc[-2]
        price, ma20 = last['종가'], last['20MA']
        macd_last, macd_prev = last['MACD_hist'], prev['MACD_hist']
        
        # -------------------------
        # 1. 기술적 분석 (Technical)
        # -------------------------
        tech_msgs = []
        if price > ma20: tech_msgs.append("20MA 위")
        else: tech_msgs.append("20MA 밑")
        
        if macd_last > 0: tech_msgs.append("MACD 양수")
        if macd_last > macd_prev: tech_msgs.append("히스토그램 증가")
        
        # -------------------------
        # 2. 직관적 분석 (Intuitive) - 방향과 에너지를 분리
        # -------------------------
        intuit_msgs = []

        # [방향 판단] 현재 주가가 어떤 길 위에 있는가?
        if price > ma20 and macd_last > 0:
            main_trend = "🚀 상승 추세 유지"
            status = "홀드"
        elif (prev['종가'] < prev['20MA']) and (price > ma20):
            main_trend = "🔥 상승 엔진 점화"
            status = "적극 매수"
        elif abs(price - ma20)/ma20 < 0.03 and macd_last > 0:
            main_trend = "⚓ 반등 준비 구간"
            status = "매수 관심"
        elif price < ma20 and macd_last < macd_prev:
            main_trend = "🧊 하락 흐름 지속"
            status = "적극 매도"
        else:
            main_trend = "🌊 방향 탐색 중"
            status = "관망"

        # [에너지 판단] 그 길 위에서 속도를 내는가, 줄이는가?
        if macd_last > macd_prev:
            energy = "📈 가속도 붙음"
        else:
            energy = "⚠️ 속도 줄어듦"

        # 두 메시지를 합쳐서 표시 (예: 🚀 상승 추세 유지 | ⚠️ 속도 줄어듦)
        intuit_msgs = [main_trend, energy]

        # 손절/익절가
        atr = last['ATR']
        sl_tp = f"{int(price - atr*2)} / {int(price + atr*2)}" if pd.notna(atr) else "- / -"

        return [code, name, int(price), status, " / ".join(tech_msgs), " | ".join(intuit_msgs), sl_tp]
    except: return None

# -------------------------
# UI 부분
# -------------------------
st.title("🛡️ 스마트 주식 스캐너 (기술 + 직관)")

st.sidebar.header("설정")
market = st.sidebar.radio("시장", ["KOSPI", "KOSDAQ"])
pages = st.sidebar.slider("분석 범위 (페이지)", 1, 5, 1)

if st.sidebar.button("분석 시작"):
    st.info("실시간으로 분석 중입니다. 아래 표를 확인하세요.")
    market_df = get_market_sum_pages(range(1, pages + 1), market)
    
    if not market_df.empty:
        results = []
        bar = st.progress(0)
        result_area = st.empty()
        
        for i, (idx, row) in enumerate(market_df.iterrows()):
            res = analyze_stock(row['종목코드'], row['종목명'])
            if res:
                results.append(res)
                df_curr = pd.DataFrame(results, columns=['코드', '종목명', '현재가', '상태', '기술적 지표', '직관적 해석', '손절/익절'])
                result_area.dataframe(df_curr.style.applymap(
                    lambda x: 'color: #ef5350; font-weight: bold' if '매수' in str(x) else ('color: #42a5f5' if '매도' in str(x) else ''),
                    subset=['상태']
                ), use_container_width=True)
            
            bar.progress((i + 1) / len(market_df))
            time.sleep(np.random.uniform(1.2, 1.8))
        
        st.success("✅ 분석이 완료되었습니다!")

