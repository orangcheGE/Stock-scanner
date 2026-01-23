import streamlit as st
import requests
from bs4 import BeautifulSoup
import pandas as pd
import numpy as np
import time
import re
import io
from datetime import datetime

# 페이지 설정 (모바일 브라우저 최적화)
st.set_page_config(page_title="주식 스캐너", layout="wide")

# -------------------------
# 크롤링 방지 설정
# -------------------------
def get_headers():
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://finance.naver.com/'
    }

# -------------------------
# 데이터 수집 함수 (에러 방지 강화)
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
            
            if not table: 
                continue

            for tr in table.select('tr'):
                tds = tr.find_all('td')
                if len(tds) < 5: continue
                a = tr.find('a', href=True)
                if not a: continue
                
                # 종목코드 추출 (NoneType 에러 방지)
                match = re.search(r'code=(\d{6})', a['href'])
                if match:
                    code = match.group(1)
                    name = a.get_text(strip=True)
                    span = tds[4].find('span')
                    change = span.get_text(strip=True) if span else '0'
                    
                    codes.append(code)
                    names.append(name)
                    changes.append(change)
            
            time.sleep(2.0) # 페이지 간 휴식
        except Exception as e:
            st.error(f"목록 로드 중 오류: {e}")
            
    return pd.DataFrame({'종목코드': codes, '종목명': names, '등락률(%)': changes})

def get_price_data(code, max_pages=15):
    url = f"https://finance.naver.com/item/sise_day.naver?code={code}"
    dfs = []
    for page in range(1, max_pages+1):
        pg_url = f"{url}&page={page}"
        try:
            res = requests.get(pg_url, headers=get_headers())
            # Pandas 경고 해결: io.StringIO 사용
            df_list = pd.read_html(io.StringIO(res.text), encoding='euc-kr')
            if df_list:
                dfs.append(df_list[0])
        except:
            continue
        time.sleep(np.random.uniform(0.5, 0.8)) # 차단 방지용 미세 딜레이
        
    if not dfs: return pd.DataFrame()
    df = pd.concat(dfs, ignore_index=True).dropna(how='all')
    df = df.rename(columns=lambda x: x.strip())
    
    for col in ['종가','시가','고가','저가','거래량']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col].astype(str).str.replace(',',''), errors='coerce')
    
    df['날짜'] = pd.to_datetime(df['날짜'], errors='coerce')
    return df.dropna(subset=['날짜','종가']).sort_values('날짜').reset_index(drop=True)

# -------------------------
# 분석 함수
# -------------------------
def analyze_stock(code, name):
    try:
        df = get_price_data(code)
        if df.empty or len(df) < 40: return None
        
        # 지표 계산
        df['20MA'] = df['종가'].rolling(20).mean()
        df['vol_ma5'] = df['거래량'].rolling(5).mean()
        
        # MACD
        ema12 = df['종가'].ewm(span=12, adjust=False).mean()
        ema26 = df['종가'].ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()
        df['MACD_hist'] = macd - signal

        last = df.iloc[-1]
        prev = df.iloc[-2]
        
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
    except:
        return None

# -------------------------
# Streamlit UI 실행부
# -------------------------
st.title("🚀 실전 20일선 스캐너")

# 사이드바 설정
st.sidebar.header("설정")
market = st.sidebar.radio("시장 선택", ["KOSPI", "KOSDAQ"])
page_range = st.sidebar.slider("가져올 페이지 수 (페이지당 50종목)", 1, 5, 1)

if st.sidebar.button("스캔 시작"):
    st.info(f"### {market} 분석 시작... (예상 소요 시간: {page_range * 2}분 내외)")
    
    market_df = get_market_sum_pages(range(1, page_range + 1), market)
    
    if market_df.empty:
        st.error("종목 목록을 가져오지 못했습니다. 잠시 후 다시 시도하세요.")
    else:
        results = []
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        total_stocks = len(market_df)
        for i, (idx, row) in enumerate(market_df.iterrows()):
            status_text.text(f"분석 중: {row['종목명']} ({i+1}/{total_stocks})")
            res = analyze_stock(row['종목코드'], row['종목명'])
            if res:
                results.append(res)
            
            progress_bar.progress((i + 1) / total_stocks)
            # 종목 간 딜레이 강화 (차단 방지 핵심)
            time.sleep(np.random.uniform(1.2, 2.0))
            
        if results:
            final_df = pd.DataFrame(results, columns=['코드', '종목명', '현재가', '20일선', '상태'])
            st.write("### 분석 완료")
            st.dataframe(final_df.style.applymap(
                lambda x: 'color: #ef5350' if '매수' in str(x) else ('color: #42a5f5' if '매도' in str(x) else ''),
                subset=['상태']
            ), use_container_width=True)

            csv = final_df.to_csv(index=False).encode('utf-8-sig')
            st.download_button("결과 다운로드(CSV)", csv, f"scan_{datetime.now().strftime('%m%d')}.csv", "text/csv")
        else:
            st.warning("분석 조건에 맞는 종목이 없거나 데이터 로드에 실패했습니다.")
