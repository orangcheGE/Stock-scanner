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
st.set_page_config(page_title="20일선 수급 정밀 스캐너", layout="wide")

def get_headers():
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://finance.naver.com/'
    }

# --- 데이터 분석 로직 ---
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
        
        # 턴어라운드/추세 확인 (최근 5일)
        last_5_ma20 = df['20MA'].iloc[-5:].values
        is_turning_up = last_5_ma20[-1] > last_5_ma20[-2]
        
        # 수급 계산 (사용자 요청 반영: 20-100%, 100-150%, 150%+)
        vol_ratio = (last['거래량'] / last['V_MA5']) if last['V_MA5'] > 0 else 1
        vol_increase_pct = (vol_ratio - 1) * 100
        
        price, ma20 = last['종가'], last['20MA']
        disparity = ((price / ma20) - 1) * 100
        
        # --- [로직 업데이트] 수급 강도 세분화 진단 ---
        status, trend = "관망", "🌊 방향 탐색"
        
        if price > ma20:
            # A. 수급 폭발 (150% 초과)
            if vol_increase_pct >= 50: # 평균 대비 1.5배 이상
                status, trend = "강력 매수", "🚀 폭발적 수급 + 강력 돌파"
            
            # B. 강력 수급 (100-150% 상승 즉, 2배-2.5배) - 사용자 피드백 반영: 100% 이상 상승 시
            elif 100 <= vol_increase_pct < 150:
                status, trend = "적극 매수", "🔥 강력한 수급 동반 상승"
            
            # C. 수급 개선 (20-100% 상승 즉, 1.2배-2배)
            elif 20 <= vol_increase_pct < 100:
                if is_turning_up:
                    status, trend = "안전 매수", "✅ 점진적 수급 개선 + 턴어라운드"
                else:
                    status, trend = "매수 검토", "📈 수급 개선 중이나 추세 확인 필요"
            
            # D. 수급 부족
            else:
                status, trend = "홀드", "📉 추세 유지 중이나 수급 약함"
        
        # 역배열에서의 에너지 반전 (바닥 탈출 신호)
        elif price < ma20 and df['MACD_hist'].iloc[-1] > df['MACD_hist'].iloc[-2]:
            if vol_increase_pct >= 20:
                status, trend = "회복 기대", "🌅 바닥 수급 유입 + 반등 준비"

        chart_url = f"https://finance.naver.com/item/fchart.naver?code={code}"
        vol_display = f"{int(vol_increase_pct)}% ↑" if vol_increase_pct >= 0 else f"{int(abs(vol_increase_pct))}% ↓"
        
        return [code, name, current_change, int(price), int(ma20), vol_display, f"{round(disparity, 2)}%", status, f"{trend}", chart_url]
    except: return None

# --- 보조 함수 및 UI (이전 기능 통합) ---
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
            time.sleep(0.2)
        except: continue
    return pd.DataFrame({'종목코드': codes, '종목명': names, '등락률': changes})

def show_styled_dataframe(dataframe):
    if dataframe.empty: return
    st.dataframe(
        dataframe.style.applymap(lambda x: 'color: #ef5350; font-weight: bold' if any(keyword in str(x) for keyword in ['적극', '안전', '강력', '매수']) else ('color: #42a5f5' if '매도' in str(x) else ''), subset=['상태'])
        .applymap(lambda x: 'color: #ef5350' if '+' in str(x) or '↑' in str(x) else ('color: #42a5f5' if '-' in str(x) or '↓' in str(x) else ''), subset=['등락률', '이격률', '거래량증가']),
        use_container_width=True,
        column_config={"차트": st.column_config.LinkColumn("차트", display_text="열기"), "코드": st.column_config.TextColumn("코드", width="small")},
        hide_index=True
    )

# --- UI 메인 ---
st.title("🛡️ 수급 강도 정밀 스캐너")
st.sidebar.header("설정")
market = st.sidebar.radio("시장 선택", ["KOSPI", "KOSDAQ"])
selected_pages = st.sidebar.multiselect("분석 페이지 선택", options=list(range(1, 41)), default=[1])
start_btn = st.sidebar.button("🚀 정밀 분석 시작")

st.subheader("📊 분석 현황")
c1, c2, c3 = st.columns(3)
total_m = c1.empty(); buy_m = c2.empty(); sell_m = c3.empty()
if 'filter' not in st.session_state: st.session_state.filter = "전체"
col1, col2, col3 = st.columns(3)
if col1.button("🔄 전체", use_container_width=True): st.session_state.filter = "전체"
if col2.button("🔴 매수 추천", use_container_width=True): st.session_state.filter = "매수"
if col3.button("🔵 매도 추천", use_container_width=True): st.session_state.filter = "매도"

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
                total_m.metric("분석 종목", f"{len(df_all)}개")
                buy_m.metric("매수 추천", f"{len(df_all[df_all['상태'].str.contains('매수|회복|강력')])}개")
                sell_m.metric("매도 추천", f"{len(df_all[df_all['상태'].str.contains('매도')])}개")
                with main_area: show_styled_dataframe(df_all)
            progress.progress((i + 1) / len(market_df))
        st.success("✅ 분석 완료")

if 'df_all' in st.session_state:
    df = st.session_state['df_all']
    display_df = df.copy()
    if st.session_state.filter == "매수": display_df = df[df['상태'].str.contains("매수|회복|강력")]
    elif st.session_state.filter == "매도": display_df = df[df['상태'].str.contains("매도")]
    with main_area: show_styled_dataframe(display_df)


