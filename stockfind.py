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
        
        # 1. 지표 계산
        df['5MA'] = df['종가'].rolling(5).mean()
        df['20MA'] = df['종가'].rolling(20).mean()
        df['V_MA5'] = df['거래량'].rolling(5).mean()
        
        # MACD (에너지 흐름)
        ema12 = df['종가'].ewm(span=12, adjust=False).mean()
        ema26 = df['종가'].ewm(span=26, adjust=False).mean()
        df['MACD_hist'] = (ema12 - ema26) - (ema12 - ema26).ewm(span=9, adjust=False).mean()
        
        # 최신 및 이전 데이터 추출
        last = df.iloc[-1]
        prev = df.iloc[-2]
        prev2 = df.iloc[-3]
        
        # 2. 정밀 수치 계산
        price = float(last['종가'])
        v_ma5 = float(last['V_MA5'])
        vol_now = float(last['거래량'])
        
        # 거래량 증가율 (0% 기준, +50%면 평균의 1.5배)
        vol_change_pct = ((vol_now / v_ma5) - 1) * 100 if v_ma5 > 0 else 0
        
        # 이격률 (0% 기준, +19%면 이평선보다 19% 떠 있음)
        gap_5ma = ((price / last['5MA']) - 1) * 100
        gap_20ma = ((price / last['20MA']) - 1) * 100
        
        m_curr, m_prev, m_prev2 = last['MACD_hist'], prev['MACD_hist'], prev2['MACD_hist']
        
        # 3. 상태 진단 로직 (사용자 피드백 반영: 이격률 리스크 우선)
        status, trend = "관망", "🌊 방향 탐색 중"

        # [필터 1] 강력 매도: 에너지가 플러스에서 마이너스로 꺾일 때 (최우선 경고)
        if m_prev > 0 and m_curr <= 0:
            status, trend = "강력 매도", "🚨 하락 전환 확정 (MACD Flip)"

        # [필터 2] 가격이 20일선 위에 있는 상승 구간
        elif price >= last['20MA']:
            
            # (A) 과열 진단: 이격률이 너무 높을 때 (15% 이상)
            if gap_20ma >= 15:
                status, trend = "과열 주의", f"🔥 이격 과다({round(gap_20ma,1)}%) / 추격 금지"
            
            # (B) 단기 이탈: 5일선을 깨고 내려올 때
            elif price < last['5MA']:
                status, trend = "추세 이탈", "⚠️ 5일선 하회 (단기 기세 꺾임)"
            
            # (C) 정상 범위 내 상승 (안전/적극 매수)
            elif m_curr > m_prev:
                # 20일선과 7% 이내일 때만 '안전' 라벨 허용
                if gap_20ma <= 7:
                    if vol_change_pct >= 50: 
                        status, trend = "적극 매수", "🚀 낮은 이격 + 수급 폭발"
                    else: 
                        status, trend = "안전 매수", "✅ 추세 전환 및 안착"
                else:
                    status, trend = "추세 보유", "📈 시세 확장 중 (보유자 영역)"
            
            # (D) 에너지 둔화 (에너지 2일 연속 하락)
            elif m_curr < m_prev < m_prev2:
                status, trend = "홀드(주의)", "📉 에너지 감속 중"
            
            else:
                status, trend = "홀드", "📈 안정적 흐름 유지"

        # [필터 3] 가격이 20일선 아래에 있는 하락 구간
        else:
            if m_curr < m_prev:
                status, trend = "하락 가속", "🧊 하락세 지속 (접근 금지)"
            else:
                status, trend = "회복 기대", "🌅 바닥 다지기 및 반등 시도"

        # 결과 데이터 구성
        chart_url = f"https://finance.naver.com/item/fchart.naver?code={code}"
        def fmt_pct(val): return f"{'+' if val > 0 else ''}{round(val, 1)}%"

        return [
            code, name, current_change, 
            int(price), 
            fmt_pct(vol_change_pct), 
            fmt_pct(gap_5ma), 
            fmt_pct(gap_20ma), 
            status, trend, chart_url
        ]
    except Exception as e:
        return None

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



