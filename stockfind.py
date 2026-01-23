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

def analyze_stock(code, name, atr_multiplier_sl=2.0, tp_muls=(2.0, 4.0)):
    try:
        df = get_price_data(code)
        if df is None or len(df) < 40: return None

        df['20MA'] = df['종가'].rolling(20).mean()
        df['vol_ma5'] = df['거래량'].rolling(5).mean()
        
        ema12 = df['종가'].ewm(span=12, adjust=False).mean()
        ema26 = df['종가'].ewm(span=26, adjust=False).mean()
        df['MACD_hist'] = (ema12 - ema26) - (ema12 - ema26).ewm(span=9, adjust=False).mean()

        df['prev_close'] = df['종가'].shift(1)
        df['TR'] = df[['고가', '저가']].max(axis=1) # 단순화된 TR 계산
        df['ATR14'] = df['TR'].rolling(14).mean()

        last = df.iloc[-1]
        prev = df.iloc[-2]
        
        price, ma20 = last['종가'], last['20MA']
        macd_last, macd_prev = last['MACD_hist'], prev['MACD_hist']
        
        price_up_trend = df['종가'].iloc[-5:].is_monotonic_increasing
        crossed_up = (prev['종가'] < prev['20MA']) and (last['종가'] > last['20MA'])
        crossed_down = (prev['종가'] > prev['20MA']) and (last['종가'] < last['20MA'])
        approaching_20 = pd.notna(ma20) and abs(price - ma20)/ma20 < 0.03
        vol_spike = last['거래량'] > (last['vol_ma5']*1.2 if pd.notna(last['vol_ma5']) else 0)

        status, debug_msgs = "관망", []

        if price_up_trend and pd.notna(ma20) and price > ma20 and macd_last > 0:
            status, debug_msgs = "홀드", ["상승추세+20MA위"]
        elif macd_last > 0 and macd_prev > 0 and price < ma20 and price_up_trend and approaching_20:
            status, debug_msgs = "매수 관심", ["20MA밑+상승추세+근접"]
        elif macd_last > 0 and crossed_up:
            status = "적극 매수" if vol_spike else "적극 관심"
            debug_msgs = ["MACD양전+20MA돌파"]
        
        recent_high5 = df['고가'].iloc[-5:].max()
        if price < recent_high5 * 0.94 and macd_last < macd_prev:
            status, debug_msgs = "매도 관심", ["고점대비하락+MACD감소"]
        if crossed_down and macd_last < macd_prev:
            status, debug_msgs = "적극 매도", ["20MA이탈+하락전환"]

        atr = last['ATR14']
        sl_tp = f"{int(price - atr*2)} / {int(price + atr*2)}" if pd.notna(atr) else "- / -"

        return [code, name, int(price), int(ma20) if pd.notna(ma20) else "-", status, sl_tp, " ".join(debug_msgs)]
    except: return None

# -------------------------
# UI 실행부 (실시간 업데이트 로직)
# -------------------------
st.title("🛡️ 실전 20일선 스캐너 (실시간 모드)")

st.sidebar.header("설정")
market = st.sidebar.radio("시장", ["KOSPI", "KOSDAQ"])
pages = st.sidebar.slider("페이지 수", 1, 5, 1)

if st.sidebar.button("분석 시작"):
    st.info("분석을 시작합니다. 종목이 한 줄씩 실시간으로 추가됩니다.")
    market_df = get_market_sum_pages(range(1, pages + 1), market)
    
    if not market_df.empty:
        results = []
        bar = st.progress(0)
        status_text = st.empty()
        result_area = st.empty() # 표가 들어갈 공간
        
        total = len(market_df)
        for i, (idx, row) in enumerate(market_df.iterrows()):
            status_text.text(f"분석 중: {row['종목명']} ({i+1}/{total})")
            res = analyze_stock(row['종목코드'], row['종목명'])
            
            if res:
                results.append(res)
                # 실시간으로 표 업데이트
                df_curr = pd.DataFrame(results, columns=['코드', '종목명', '현재가', '20MA', '상태', '손절/익절', '분석근거'])
                result_area.dataframe(df_curr.style.applymap(
                    lambda x: 'background-color: #ffcccc' if '매수' in str(x) else ('background-color: #cce5ff' if '매도' in str(x) else ''),
                    subset=['상태']
                ), use_container_width=True)
            
            bar.progress((i + 1) / total)
            time.sleep(np.random.uniform(1.2, 1.8))
        
        status_text.success("✅ 모든 분석이 완료되었습니다!")
        if results:
            st.download_button("결과 CSV 저장", pd.DataFrame(results).to_csv(index=False).encode('utf-8-sig'), "result.csv")
