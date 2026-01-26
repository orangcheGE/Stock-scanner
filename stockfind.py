import streamlit as st
import requests
from bs4 import BeautifulSoup
import pandas as pd
import numpy as np
import time
import re
import io

# 1. 페이지 설정 및 헤더
st.set_page_config(page_title="20일선 수급/이격 정밀 진단", layout="wide")

def get_headers():
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://finance.naver.com/'
    }

# --- [핵심] 정밀 분석 로직 (5일선 이탈 & 이격 리스크 포함) ---
def analyze_stock(code, name, current_change):
    try:
        # 데이터 가져오기 (충분한 데이터 확보)
        df = get_price_data(code)
        if df is None or len(df) < 40: return None
        
        # 지표 계산
        df['5MA'] = df['종가'].rolling(5).mean()
        df['20MA'] = df['종가'].rolling(20).mean()
        df['V_MA5'] = df['거래량'].rolling(5).mean()
        
        # MACD 에너지 계산
        ema12 = df['종가'].ewm(span=12, adjust=False).mean()
        ema26 = df['종가'].ewm(span=26, adjust=False).mean()
        df['MACD_hist'] = (ema12 - ema26) - (ema12 - ema26).ewm(span=9, adjust=False).mean()
        
        last = df.iloc[-1]
        prev = df.iloc[-2]
        prev2 = df.iloc[-3]
        
        price = float(last['종가'])
        ma5 = float(last['5MA'])
        ma20 = float(last['20MA'])
        v_ma5 = float(last['V_MA5'])
        vol_now = float(last['거래량'])
        
        # 수치 계산 (순수 숫자 데이터로 유지)
        vol_change_pct = round(((vol_now / v_ma5) - 1) * 100, 1) if v_ma5 > 0 else 0
        gap_20ma = round(((price / ma20) - 1) * 100, 1)
        
        m_curr, m_prev, m_prev2 = last['MACD_hist'], prev['MACD_hist'], prev2['MACD_hist']
        
        status, trend = "관망", "🌊 방향 탐색"

        # --- 상태 판정 로직 ---
        if m_prev > 0 and m_curr <= 0:
            status, trend = "강력 매도", "🚨 에너지 데드크로스 발생"
            
        elif price >= ma20:
            # 1. 과열 체크 (이격 10% 이상은 무조건 과열)
            if gap_20ma >= 10: 
                status, trend = "과열 주의", f"🔥 이격 과다({gap_20ma}%) / 추격 금지"
            # 2. 5일선 이탈 체크 (사용자 강조 로직)
            elif price < ma5:
                status, trend = "추세 이탈", "⚠️ 5일선 하회 (기세 꺾임)"
            # 3. 정상 상승 구간
            elif m_curr > m_prev:
                status = "적극 매수" if gap_20ma <= 5 and vol_change_pct >= 30 else "안전 매수"
                trend = "✅ 추세선 위 안착 상승"
            # 4. 에너지 둔화 (2일 연속 감소)
            elif m_curr < m_prev < m_prev2:
                status, trend = "홀드(주의)", "📉 에너지 감속 중"
            else:
                status, trend = "홀드", "📈 안정권 유지"
        else:
            status, trend = "하락 가속", "🧊 접근 금지" if m_curr < m_prev else "🌅 바닥 다지기"

        chart_url = f"https://finance.naver.com/item/fchart.naver?code={code}"

        return [code, name, current_change, int(price), int(ma20), vol_change_pct, gap_20ma, status, trend, chart_url]
    except: return None

# --- 데이터 수집 보조 함수 ---
def get_price_data(code):
    url = f"https://finance.naver.com/item/sise_day.naver?code={code}"
    try:
        res = requests.get(url + "&page=1", headers=get_headers())
        df = pd.read_html(io.StringIO(res.text), encoding='euc-kr')[0]
        for col in ['종가','거래량']:
            df[col] = pd.to_numeric(df[col].astype(str).str.replace(',',''), errors='coerce')
        return df.dropna(subset=['종가']).sort_values('날짜').reset_index(drop=True)
    except: return None

# --- [복구] 시장 및 페이지별 종목 리스트 수집 ---
def get_market_sum_pages(page_list, market="KOSPI"):
    sosok = 0 if market == "KOSPI" else 1
    codes, names, changes = [], [], []
    for page in page_list:
        url = f"https://finance.naver.com/sise/sise_market_sum.naver?sosok={sosok}&page={page}"
        try:
            res = requests.get(url, headers=get_headers())
            res.encoding = 'euc-kr'
            soup = BeautifulSoup(res.text, 'html.parser')
            rows = soup.select('table.type_2 tr')
            for tr in rows:
                tds = tr.find_all('td')
                a_tag = tr.find('a', href=True)
                if len(tds) < 5 or not a_tag: continue
                
                code_match = re.search(r'code=(\d{6})', a_tag['href'])
                if code_match:
                    codes.append(code_match.group(1))
                    names.append(a_tag.get_text(strip=True))
                    changes.append(tds[4].get_text(strip=True))
            time.sleep(0.1) 
        except: continue
    return pd.DataFrame({'종목코드': codes, '종목명': names, '등락률': changes})

# --- UI 스타일링 ---
def show_styled_dataframe(df):
    if df.empty: return
    st.dataframe(
        df.style.format({'거래량증가': '{:+.1f}%', '이격률': '{:+.1f}%'})
        .applymap(lambda x: 'color: #ef5350; font-weight: bold' if any(k in str(x) for k in ['매수', '적극']) else ('color: #42a5f5; font-weight: bold' if any(k in str(x) for k in ['매도', '이탈', '과열']) else ''), subset=['상태'])
        .applymap(lambda x: 'color: #ef5350' if isinstance(x, (int, float)) and x > 0 else ('color: #42a5f5' if isinstance(x, (int, float)) and x < 0 else ''), subset=['등락률', '거래량증가', '이격률']),
        use_container_width=True,
        column_config={"차트": st.column_config.LinkColumn("차트", display_text="열기")},
        hide_index=True
    )

# --- [복구 완료] 메인 실행부 및 사이드바 버튼 ---
st.title("🛡️ 수급/이격 정밀 진단 시스템 v5.0")

# 사이드바 버튼 및 필터 복구
market = st.sidebar.radio("📈 시장 선택", ["KOSPI", "KOSDAQ"])
page_options = list(range(1, 11))
pages = st.sidebar.multiselect("📄 분석 페이지 선택 (페이지당 50종목)", options=page_options, default=[1])

if 'df_all' not in st.session_state:
    st.session_state.df_all = pd.DataFrame()

if st.sidebar.button("🚀 정밀 분석 시작"):
    market_df = get_market_sum_pages(pages, market)
    if not market_df.empty:
        results = []
        prog_bar = st.progress(0)
        for i, (idx, row) in enumerate(market_df.iterrows()):
            res = analyze_stock(row['종목코드'], row['종목명'], row['등락률'])
            if res:
                results.append(res)
            prog_bar.progress((i + 1) / len(market_df))
        
        cols = ['코드', '종목명', '등락률', '현재가', '20MA', '거래량증가', '이격률', '상태', '해석', '차트']
        st.session_state.df_all = pd.DataFrame(results, columns=cols)
    else:
        st.error("데이터를 가져오는 중 오류가 발생했습니다.")

if not st.session_state.df_all.empty:
    show_styled_dataframe(st.session_state.df_all)
