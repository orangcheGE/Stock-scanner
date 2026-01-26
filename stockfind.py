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

# --- [핵심] 정밀 분석 로직 (데이터 순서 및 수식 완전 수정) ---
def analyze_stock(code, name, current_change):
    try:
        df = get_price_data(code)
        if df is None or len(df) < 40: return None
        
        # 지표 계산
        df['5MA'] = df['종가'].rolling(5).mean()
        df['20MA'] = df['종가'].rolling(20).mean()
        df['V_MA5'] = df['거래량'].rolling(5).mean()
        
        # MACD (에너지)
        ema12 = df['종가'].ewm(span=12, adjust=False).mean()
        ema26 = df['종가'].ewm(span=26, adjust=False).mean()
        df['MACD_hist'] = (ema12 - ema26) - (ema12 - ema26).ewm(span=9, adjust=False).mean()
        
        last = df.iloc[-1]
        prev = df.iloc[-2]
        prev2 = df.iloc[-3]
        
        # 데이터 추출
        price = float(last['종가'])
        ma5 = float(last['5MA'])
        ma20 = float(last['20MA'])
        v_ma5 = float(last['V_MA5'])
        vol_now = float(last['거래량'])
        
        # 1. 거래량 증가율 (5일 평균 대비 증감분 %)
        vol_change_pct = ((vol_now / v_ma5) - 1) * 100 if v_ma5 > 0 else 0
        
        # 2. 이격률 (단순 괴리율: 주가가 이평선보다 몇 % 위에 있는가)
        gap_20ma = ((price / ma20) - 1) * 100
        
        m_curr, m_prev, m_prev2 = last['MACD_hist'], prev['MACD_hist'], prev2['MACD_hist']
        
        status, trend = "관망", "방향 탐색"

        # --- 판정 로직 ---
        if m_prev > 0 and m_curr <= 0:
            status, trend = "강력 매도", "🚨 하락 전환 확정 (MACD Flip)"
        elif price >= ma20:
            if gap_20ma >= 10: # 이격률 10% 이상 과열 필터
                status, trend = "과열 주의", f"🔥 이격 과다({round(gap_20ma,1)}%) / 추격 금지"
            elif price < ma5:
                status, trend = "추세 이탈", "⚠️ 5일선 하향 이탈 (단기 기세 꺾임)"
            elif m_curr > m_prev:
                # 이격률이 낮을 때만 안전/적극 매수
                status = "적극 매수" if gap_20ma <= 5 and vol_change_pct >= 30 else "안전 매수"
                trend = "✅ 안정적 우상향"
            elif m_curr < m_prev < m_prev2:
                status, trend = "홀드(주의)", "📉 에너지 감속 중"
            else:
                status, trend = "홀드", "📈 추세 유지"
        else:
            status, trend = "하락 가속", "🧊 접근 금지" if m_curr < m_prev else "🌅 바닥 다지기"

        chart_url = f"https://finance.naver.com/item/fchart.naver?code={code}"
        
        # 수치 포맷팅 (부호 포함)
        def fmt(v): return f"{'+' if v > 0 else ''}{round(v, 1)}%"

        # 반환 순서 (매우 중요: DataFrame 컬럼 순서와 일치해야 함)
        return [
            code,               # 코드
            name,               # 종목명
            current_change,     # 등락률
            int(price),         # 현재가
            int(ma20),          # 20MA (가격을 숫자로!)
            fmt(vol_change_pct),# 거래량증가
            fmt(gap_20ma),      # 이격률
            status,             # 상태
            trend,              # 해석
            chart_url           # 차트
        ]
    except: return None

# --- 데이터 수집 및 UI (전체 통합) ---
def get_price_data(code, max_pages=15):
    url = f"https://finance.naver.com/item/sise_day.naver?code={code}"
    try:
        res = requests.get(url + "&page=1", headers=get_headers())
        df = pd.read_html(io.StringIO(res.text), encoding='euc-kr')[0]
        # 실전에서는 더 많은 데이터를 가져오지만 예시를 위해 간략화
        for col in ['종가','거래량']:
            df[col] = pd.to_numeric(df[col].astype(str).str.replace(',',''), errors='coerce')
        return df.dropna(subset=['종가']).sort_values('날짜').reset_index(drop=True)
    except: return None

def get_market_sum_pages(page_list, market="KOSPI"):
    sosok = 0 if market == "KOSPI" else 1
    codes, names, changes = [], [], []
    for page in page_list:
        url = f"https://finance.naver.com/sise/sise_market_sum.naver?sosok={sosok}&page={page}"
        res = requests.get(url, headers=get_headers()); res.encoding = 'euc-kr'
        soup = BeautifulSoup(res.text, 'html.parser')
        for tr in soup.select('table.type_2 tr'):
            tds = tr.find_all('td')
            if len(tds) < 5 or not tr.find('a'): continue
            codes.append(re.search(r'code=(\d{6})', tr.find('a')['href']).group(1))
            names.append(tr.find('a').get_text(strip=True))
            changes.append(tds[4].get_text(strip=True))
    return pd.DataFrame({'종목코드': codes, '종목명': names, '등락률': changes})

def show_styled_dataframe(dataframe):
    if dataframe.empty: return
    st.dataframe(
        dataframe.style.applymap(lambda x: 'color: #ef5350; font-weight: bold' if any(k in str(x) for k in ['매수', '적극']) else ('color: #42a5f5; font-weight: bold' if any(k in str(x) for k in ['매도', '이탈', '과열']) else ''), subset=['상태'])
        .applymap(lambda x: 'color: #ef5350' if '+' in str(x) else ('color: #42a5f5' if '-' in str(x) else ''), subset=['등락률', '거래량증가', '이격률']),
        use_container_width=True,
        column_config={"차트": st.column_config.LinkColumn("차트", display_text="열기")},
        hide_index=True
    )

st.title("🛡️ 수급/이격 정밀 진단 v3.0 (수치 보정 완료)")
if st.sidebar.button("🚀 정밀 분석 시작"):
    market_df = get_market_sum_pages([1], "KOSPI")
    results = [analyze_stock(c, n, r) for c, n, r in zip(market_df['종목코드'], market_df['종목명'], market_df['등락률'])]
    results = [r for r in results if r]
    # 이미지와 동일한 컬럼 순서 선언
    cols = ['코드', '종목명', '등락률', '현재가', '20MA', '거래량증가', '이격률', '상태', '해석', '차트']
    st.session_state.df_all = pd.DataFrame(results, columns=cols)
    show_styled_dataframe(st.session_state.df_all)
