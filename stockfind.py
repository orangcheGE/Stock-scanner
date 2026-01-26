import streamlit as st
import requests
from bs4 import BeautifulSoup
import pandas as pd
import numpy as np
import time
import re
import io
import urllib.parse

# 1. 페이지 설정
st.set_page_config(page_title="20일선 정밀 진단 시스템", layout="wide")

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
    for col in ['종가','고가','저가','거래량']:
        if col in df.columns: df[col] = pd.to_numeric(df[col].astype(str).str.replace(',',''), errors='coerce')
    df['날짜'] = pd.to_datetime(df['날짜'], errors='coerce')
    return df.dropna(subset=['날짜','종가']).sort_values('날짜').reset_index(drop=True)

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
                    codes.append(match.group(1)); names.append(a.get_text(strip=True)); changes.append(tds[4].get_text(strip=True))
            time.sleep(0.15)
        except: continue
    return pd.DataFrame({'종목코드': codes, '종목명': names, '등락률': changes})

# --- [핵심] 정밀 분석 로직 ---
def analyze_stock(code, name, current_change):
    try:
        df = get_price_data(code)
        if df is None or len(df) < 40: return None
        
        # 지표 계산
        df['20MA'] = df['종가'].rolling(20).mean()
        df['V_MA5'] = df['거래량'].rolling(5).mean()
        ema12 = df['종가'].ewm(span=12, adjust=False).mean()
        ema26 = df['종가'].ewm(span=26, adjust=False).mean()
        df['MACD_hist'] = (ema12 - ema26) - (ema12 - ema26).ewm(span=9, adjust=False).mean()
        
        last = df.iloc[-1]
        prev = df.iloc[-2]
        prev2 = df.iloc[-3]
        
        # 최근 5일 데이터 (추세 확인용)
        last_5_days = df.iloc[-5:]
        # 5일 내내 종가가 20일선 위에 있었는지 체크
        is_above_5d = (last_5_days['종가'] > last_5_days['20MA']).all()
        
        price, ma20 = last['종가'], last['20MA']
        m_curr, m_prev, m_prev2 = last['MACD_hist'], prev['MACD_hist'], prev2['MACD_hist']
        vol_ratio = (last['거래량'] / last['V_MA5']) if last['V_MA5'] > 0 else 1
        vol_pct = (vol_ratio - 1) * 100
        disparity = ((price / ma20) - 1) * 100
        
        status, trend = "관망", "🌊 방향 탐색 중"
        
        # 1. 주가가 20일선 위에 있는 경우
        if price > ma20:
            # 강력 매도 (MACD 플러스에서 마이너스로 확정 전환 시에만)
            if m_prev > 0 and m_curr <= 0:
                status, trend = "강력 매도", "🚨 에너지 데드크로스 발생 (추세 반전)"
            
            # [사용자 피드백 반영] 에너지가 2일 하락하더라도 5일간 추세선 위라면 '홀드'
            elif m_curr > 0 and (m_curr < m_prev < m_prev2):
                if is_above_5d:
                    status, trend = "홀드", "📈 에너지는 쉬어가나 추세선 위 안착 중"
                else:
                    status, trend = "매도 관심", "⚠️ 추세 불안정 + 에너지 둔화"
            
            # 매수 신호 (수급 중심)
            elif m_curr > m_prev:
                if vol_pct >= 100: status, trend = "강력 매수", "🚀 압도적 수급 + 추세 강화"
                elif 20 <= vol_pct < 100: status, trend = "적극 매수", "🔥 수급 동반 우상향"
                else: status, trend = "안전 매수", "✅ 추세 유지 및 점진적 상승"
            
            else:
                status, trend = "홀드", "📈 안정적 흐름 유지"

        # 2. 주가가 20일선 아래에 있는 경우
        elif price < ma20:
            if m_curr < m_prev: status, trend = "하락 가속", "🧊 하락 추세 진행 (신규 진입 금지)"
            elif m_curr > m_prev: status, trend = "회복 기대", "🌅 바닥권 에너지 반전 시도"

        chart_url = f"https://finance.naver.com/item/fchart.naver?code={code}"
        vol_display = f"{int(vol_pct)}% ↑" if vol_pct >= 0 else f"{int(abs(vol_pct))}% ↓"
        
        return [code, name, current_change, int(price), int(ma20), vol_display, f"{round(disparity, 2)}%", status, f"{trend}", chart_url]
    except: return None

# --- UI 스타일링 ---
def show_styled_dataframe(dataframe):
    if dataframe.empty: return
    def color_status(val):
        if '강력 매수' in val: return 'background-color: #ffcccc; color: #cc0000; font-weight: bold'
        if '적극 매수' in val or '안전 매수' in val: return 'color: #ef5350; font-weight: bold'
        if '강력 매도' in val: return 'background-color: #cce5ff; color: #004085; font-weight: bold'
        if '매도 관심' in val or '하락 가속' in val: return 'color: #42a5f5; font-weight: bold'
        return ''

    st.dataframe(
        dataframe.style.applymap(color_status, subset=['상태'])
        .applymap(lambda x: 'color: #ef5350' if '+' in str(x) or '↑' in str(x) else ('color: #42a5f5' if '-' in str(x) or '↓' in str(x) else ''), subset=['등락률', '이격률', '거래량증가']),
        use_container_width=True,
        column_config={"차트": st.column_config.LinkColumn("차트", display_text="열기"), "코드": st.column_config.TextColumn("코드", width="small")},
        hide_index=True
    )

# --- 메인 실행 UI ---
st.title("🛡️ 실전형 수급 & 에너지 정밀 스캐너")
st.sidebar.header("🔍 분석 설정")
market = st.sidebar.radio("시장 선택", ["KOSPI", "KOSDAQ"])
selected_pages = st.sidebar.multiselect("분석 페이지 (1p=50개)", options=list(range(1, 41)), default=[1])
start_btn = st.sidebar.button("🚀 정밀 분석 시작")

st.subheader("📊 리얼타임 시장 진단")
c1, c2, c3, c4 = st.columns(4)
total_m = c1.empty(); buy_m = c2.empty(); watch_m = c3.empty(); sell_m = c4.empty()

if 'filter' not in st.session_state: st.session_state.filter = "전체"
col1, col2, col3, col4 = st.columns(4)
if col1.button("🔄 전체 리스트", use_container_width=True): st.session_state.filter = "전체"
if col2.button("🔴 매수 추천 (적극/안전)", use_container_width=True): st.session_state.filter = "매수"
if col3.button("🟡 매도 관심 (탄력둔화)", use_container_width=True): st.session_state.filter = "관심"
if col4.button("🔵 강력 매도 (추세파괴)", use_container_width=True): st.session_state.filter = "매도"

main_area = st.empty()

if start_btn:
    market_df = get_market_sum_pages(selected_pages, market)
    if not market_df.empty:
        results = []
        progress = st.progress(0)
        for i, (idx, row) in enumerate(market_df.iterrows()):
            res = analyze_stock(row['종목코드'], row['종목명'], row['등락률'])
            if res:
                results.append(res)
                df_all = pd.DataFrame(results, columns=['코드', '종목명', '등락률', '현재가', '20MA', '거래량증가', '이격률', '상태', '해석', '차트'])
                st.session_state['df_all'] = df_all
                total_m.metric("분석 대상", f"{len(df_all)}개")
                buy_m.metric("매수 추천", f"{len(df_all[df_all['상태'].str.contains('매수')])}개")
                watch_m.metric("매도 관심", f"{len(df_all[df_all['상태'].str.contains('관심|경계')])}개")
                sell_m.metric("강력 매도", f"{len(df_all[df_all['상태'].str.contains('강력 매도')])}개")
                with main_area: show_styled_dataframe(df_all)
            progress.progress((i + 1) / len(market_df))
        st.success("✅ 진단이 완료되었습니다.")

if 'df_all' in st.session_state:
    df = st.session_state['df_all']
    display_df = df.copy()
    if st.session_state.filter == "매수": display_df = df[df['상태'].str.contains("매수")]
    elif st.session_state.filter == "관심": display_df = df[df['상태'].str.contains("관심|경계")]
    elif st.session_state.filter == "매도": display_df = df[df['상태'].str.contains("강력 매도")]
    with main_area: show_styled_dataframe(display_df)

