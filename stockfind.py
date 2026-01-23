import streamlit as st
import requests
from bs4 import BeautifulSoup
import pandas as pd
import io
import numpy as np
import time
import re
from datetime import datetime

# 페이지 설정 (모바일 브라우저 최적화)
st.set_page_config(page_title="주식 스캐너", layout="wide")

# -------------------------
# 크롤링 방지 설정
# -------------------------
def get_headers():
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
        'Referer': 'https://finance.naver.com/'
    }

# -------------------------
# 데이터 수집 함수 (딜레이 강화)
# -------------------------
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
                
                code = re.search(r'code=(\d{6})', a['href']).group(1)
                name = a.get_text(strip=True)
                span = tds[4].find('span')
                change = span.get_text(strip=True) if span else '0'
                
                codes.append(code)
                names.append(name)
                changes.append(change)
            
            # 페이지 전환 간 넉넉한 휴식 (2~3초)
            time.sleep(2.5) 
        except Exception as e:
            st.error(f"목록 로드 중 오류: {e}")
            
    return pd.DataFrame({'종목코드': codes, '종목명': names, '등락률(%)': changes})

def get_price_data(code, max_pages=15):
    url = f"https://finance.naver.com/item/sise_day.naver?code={code}"
    dfs = []
    for page in range(1, max_pages+1):
        pg_url = f"{url}&page={page}"
        res = requests.get(pg_url, headers=get_headers())
        try:
            df = pd.read_html(io.StringIO(res.text), encoding='euc-kr')[0]
            dfs.append(df)
        except:
            continue
        # 페이지별 0.5~1초 랜덤 딜레이
        time.sleep(np.random.uniform(0.5, 1.0))
        
    if not dfs: return pd.DataFrame()
    df = pd.concat(dfs, ignore_index=True).dropna(how='all')
    df = df.rename(columns=lambda x: x.strip())
    for col in ['종가','시가','고가','저가','거래량']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col].astype(str).str.replace(',',''), errors='coerce')
    df['날짜'] = pd.to_datetime(df['날짜'], errors='coerce')
    return df.dropna(subset=['날짜','종가']).sort_values('날짜').reset_index(drop=True)

# -------------------------
# 분석 로직 (기존 로직 유지)
# -------------------------
def analyze_stock(code, name):
    df = get_price_data(code)
    if len(df) < 40: return None
    
    # 지표 계산
    df['20MA'] = df['종가'].rolling(20).mean()
    df['vol_ma5'] = df['거래량'].rolling(5).mean()
    
    # MACD
    ema12 = df['종가'].ewm(span=12, adjust=False).mean()
    ema26 = df['종가'].ewm(span=26, adjust=False).mean()
    df['MACD_hist'] = (ema12 - ema26) - (ema12 - ema26).ewm(span=9, adjust=False).mean()

    last = df.iloc[-1]
    prev = df.iloc[-2]
    
    # 조건 체크
    price = last['종가']
    ma20 = last['20MA']
    macd_last = last['MACD_hist']
    macd_prev = prev['MACD_hist']
    
    status = "관망"
    if price > ma20 and macd_last > 0: status = "홀드"
    if prev['종가'] < prev['20MA'] and price > ma20 and macd_last > 0:
        status = "적극 매수" if last['거래량'] > last['vol_ma5'] * 1.2 else "매수 관심"
    if price < ma20 and macd_last < macd_prev: status = "적극 매도"

    return [code, name, price, round(ma20, 0), status]

# -------------------------
# Streamlit UI
# -------------------------
st.title("🚀 실전 20일선 스캐너")
st.sidebar.header("설정")

market = st.sidebar.radio("시장 선택", ["KOSPI", "KOSDAQ"])
page_range = st.sidebar.slider("가져올 페이지 수", 1, 5, 1)

if st.sidebar.button("스캔 시작"):
    st.write(f"### {market} 분석 중... (차단 방지를 위해 천천히 진행합니다)")
    
    market_df = get_market_sum_pages(range(1, page_range + 1), market)
    results = []
    
    progress_bar = st.progress(0)
    for i, (idx, row) in enumerate(market_df.iterrows()):
        res = analyze_stock(row['종목코드'], row['종목명'])
        if res:
            results.append(res)
        
        # 진행률 업데이트
        progress_bar.progress((i + 1) / len(market_df))
        # 종목간 딜레이 (1.5~2.5초로 넉넉하게 설정)
        time.sleep(np.random.uniform(1.5, 2.5))
        
    final_df = pd.DataFrame(results, columns=['코드', '종목명', '현재가', '20일선', '상태'])
    
    # 결과 출력
    st.write("### 분석 결과")
    st.dataframe(final_df.style.applymap(
        lambda x: 'color: red' if '매수' in str(x) else ('color: blue' if '매도' in str(x) else ''),
        subset=['상태']
    ), use_container_width=True)

    # CSV 다운로드 버튼
    csv = final_df.to_csv(index=False).encode('utf-8-sig')
    st.download_button("결과 다운로드(CSV)", csv, "result.csv", "text/csv")

