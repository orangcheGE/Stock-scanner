import streamlit as st
import requests
from bs4 import BeautifulSoup
import pandas as pd
import numpy as np
import time
import re
import io

# 1. 페이지 설정
st.set_page_config(page_title="20일선 수급/이격 정밀 진단", layout="wide")

def get_headers():
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://finance.naver.com/'
    }

# --- 데이터 수집 함수 ---
def get_price_data(code, max_pages=15):
    url = f"https://finance.naver.com/item/sise_day.naver?code={code}"
    dfs = []
    for page in range(1, max_pages+1):
        try:
            res = requests.get(f"{url}&page={page}", headers=get_headers())
            df_list = pd.read_html(io.StringIO(res.text), encoding='euc-kr')
            if df_list: dfs.append(df_list[0])
        except: continue
    if not dfs: return None
    df = pd.concat(dfs, ignore_index=True).dropna(how='all')
    df = df.rename(columns=lambda x: x.strip())
    for col in ['종가','거래량']:
        if col in df.columns: df[col] = pd.to_numeric(df[col].astype(str).str.replace(',',''), errors='coerce')
    return df.dropna(subset=['종가']).sort_values('날짜').reset_index(drop=True)

# --- [핵심] 정밀 분석 로직 (이미지 오류 완벽 수정) ---
def analyze_stock(code, name, current_change):
    try:
        df = get_price_data(code)
        if df is None or len(df) < 40: return None
        
        # 지표 계산
        df['5MA'] = df['종가'].rolling(5).mean()
        df['20MA'] = df['종가'].rolling(20).mean()
        df['V_MA5'] = df['거래량'].rolling(5).mean()
        
        # MACD (에너지 흐름)
        ema12 = df['종가'].ewm(span=12, adjust=False).mean()
        ema26 = df['종가'].ewm(span=26, adjust=False).mean()
        df['MACD_hist'] = (ema12 - ema26) - (ema12 - ema26).ewm(span=9, adjust=False).mean()
        
        last = df.iloc[-1]
        prev = df.iloc[-2]
        prev2 = df.iloc[-3]
        
        # 1. 수치 추출 (실제 가격/수량)
        price = float(last['종가'])
        ma5 = float(last['5MA'])
        ma20 = float(last['20MA'])
        v_ma5 = float(last['V_MA5'])
        vol_now = float(last['거래량'])
        
        # 2. 거래량 증가율 (사용자 기준: 증감분 %)
        vol_change_pct = ((vol_now / v_ma5) - 1) * 100 if v_ma5 > 0 else 0
        
        # 3. 이격률 (0% 기준 괴리율)
        gap_20ma = ((price / ma20) - 1) * 100
        
        m_curr, m_prev, m_prev2 = last['MACD_hist'], prev['MACD_hist'], prev2['MACD_hist']
        
        # 4. 상태 진단 로직
        status, trend = "관망", "🌊 방향 탐색"

        # [필터 1] MACD 하락 전환 (강력 매도)
        if m_prev > 0 and m_curr <= 0:
            status, trend = "강력 매도", "🚨 에너지 데드크로스 (하락 전환)"
        
        # [필터 2] 상승 권역 (20일선 위)
        elif price >= ma20:
            if gap_20ma >= 12: # 이격 12% 이상 시 과열 경고 (조정 가능)
                status, trend = "과열 주의", f"🔥 이격 과다({round(gap_20ma,1)}%) / 추격 금지"
            elif price < ma5: # 5일선 이탈
                status, trend = "추세 이탈", "⚠️ 5일선 하향 돌파 (기세 꺾임)"
            elif m_curr > m_prev:
                if gap_20ma <= 5: status, trend = "적극 매수", "🚀 낮은 이격 + 수급 폭발"
                else: status, trend = "안전 매수", "✅ 추세 유지"
            elif m_curr < m_prev < m_prev2:
                status, trend = "홀드(주의)", "📉 에너지 감속 중"
            else:
                status, trend = "홀드", "📈 안정적 안착"
        
        # [필터 3] 하락 권역 (20일선 아래)
        else:
            status, trend = "하락 가속", "🧊 접근 금지" if m_curr < m_prev else "🌅 바닥 다지기"

        chart_url = f"https://finance.naver.com/item/fchart.naver?code={code}"
        def fmt(v): return f"{'+' if v > 0 else ''}{round(v, 1)}%"

        # 이미지의 컬럼 순서와 정확히 매칭 (코드, 종목명, 등락률, 현재가, 20MA, 거래량증가, 이격률, 상태, 해석, 차트)
        return [
            code, name, current_change, 
            int(price), 
            int(ma20),          # 20MA (가격으로 정상 출력)
            fmt(vol_change_pct),# 거래량증가 (증감분%)
            fmt(gap_20ma),      # 이격률 (괴리율%)
            status, trend, chart_url
        ]
    except: return None

# --- 시장 데이터 수집 ---
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
                if a:
                    codes.append(re.search(r'code=(\d{6})', a['href']).group(1))
                    names.append(a.get_text(strip=True))
                    changes.append(tds[4].get_text(strip=True))
        except: continue
    return pd.DataFrame({'종목코드': codes, '종목명': names, '등락률': changes})

# --- UI 스타일링 ---
def show_styled_dataframe(dataframe):
    if dataframe.empty: return
    st.dataframe(
        dataframe.style.applymap(lambda x: 'color: #ef5350; font-weight: bold' if any(k in str(x) for k in ['매수', '적극']) and '매도' not in str(x) else ('color: #42a5f5; font-weight: bold' if any(k in str(x) for k in ['매도', '이탈', '과열']) else ''), subset=['상태'])
        .applymap(lambda x: 'color: #ef5350' if '+' in str(x) else ('color: #42a5f5' if '-' in str(x) else ''), subset=['등락률', '거래량증가', '이격률']),
        use_container_width=True,
        column_config={"차트": st.column_config.LinkColumn("차트", display_text="열기")},
        hide_index=True
    )

# --- 메인 앱 ---
st.title("🛡️ 실전 수급 & 20일선 정밀 진단 시스템")
market = st.sidebar.radio("시장 선택", ["KOSPI", "KOSDAQ"])
pages = st.sidebar.multiselect("분석 페이지(1-5)", options=list(range(1, 6)), default=[1])
start = st.sidebar.button("🚀 정밀 분석 시작")

if 'df_all' not in st.session_state: st.session_state.df_all = pd.DataFrame()

if start:
    market_df = get_market_sum_pages(pages, market)
    results = []
    prog = st.progress(0)
    for i, (idx, row) in enumerate(market_df.iterrows()):
        res = analyze_stock(row['종목코드'], row['종목명'], row['등락률'])
        if res:
            results.append(res)
            # 표의 컬럼명을 이미지와 완벽히 일치시킴
            cols = ['코드', '종목명', '등락률', '현재가', '20MA', '거래량증가', '이격률', '상태', '해석', '차트']
            st.session_state.df_all = pd.DataFrame(results, columns=cols)
            with st.empty(): show_styled_dataframe(st.session_state.df_all)
        prog.progress((i + 1) / len(market_df))

if not st.session_state.df_all.empty:
    show_styled_dataframe(st.session_state.df_all)
