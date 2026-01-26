import streamlit as st
import requests
from bs4 import BeautifulSoup
import pandas as pd
import numpy as np
import time
import re
import io
import urllib.parse
from datetime import datetime

# 1. 페이지 설정
st.set_page_config(page_title="20일선 스마트 대시보드 V2", layout="wide")

def get_headers():
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://finance.naver.com/'
    }

# --- 데이터 수집 및 분석 ---
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
                    codes.append(match.group(1)); names.append(a.get_text(strip=True))
                    changes.append(tds[4].get_text(strip=True))
            time.sleep(0.3)
        except: continue
    return pd.DataFrame({'종목코드': codes, '종목명': names, '등락률': changes})

def get_price_data(code, max_pages=15):
    url = f"https://finance.naver.com/item/sise_day.naver?code={code}"
    dfs = []
    for page in range(1, max_pages+1):
        try:
            res = requests.get(f"{url}&page={page}", headers=get_headers())
            df_list = pd.read_html(io.StringIO(res.text), encoding='euc-kr')
            if df_list: dfs.append(df_list[0])
        except: continue
    if not dfs: return pd.DataFrame()
    df = pd.concat(dfs, ignore_index=True).dropna(how='all')
    df = df.rename(columns=lambda x: x.strip())
    for col in ['종가','고가','저가','거래량']:
        if col in df.columns: df[col] = pd.to_numeric(df[col].astype(str).str.replace(',',''), errors='coerce')
    df['날짜'] = pd.to_datetime(df['날짜'], errors='coerce')
    return df.dropna(subset=['날짜','종가']).sort_values('날짜').reset_index(drop=True)

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
        df['tr'] = np.maximum(df['고가'] - df['저가'], np.maximum(abs(df['고가'] - df['종가'].shift(1)), abs(df['저가'] - df['종가'].shift(1))))
        df['ATR'] = df['tr'].rolling(14).mean()
        
        last, prev = df.iloc[-1], df.iloc[-2]
        price, ma20, macd_last, macd_prev = last['종가'], last['20MA'], last['MACD_hist'], prev['MACD_hist']
        vol_ratio = last['거래량'] / last['V_MA5'] if last['V_MA5'] > 0 else 1
        disparity = ((price / ma20) - 1) * 100
        
        # 기본 수치
        diff = price - ma20
        disparity_fmt = f"{'+' if disparity > 0 else ''}{round(disparity, 2)}%"
        sl_tp = f"{int(price - last['ATR']*2)} / {int(price + last['ATR']*2)}" if pd.notna(last['ATR']) else "- / -"
        
        # --- 리스크 가중 진단 로직 ---
        status, trend = "관망", "🌊 방향 탐색"
        
        if price > ma20 and macd_last > 0:
            if 0 <= disparity <= 3:
                status, trend = "안전 매수", "✅ 골디락스 (안정적 상승)"
            elif disparity > 10:
                # 거래량이 동반된 과열인지 확인
                if vol_ratio > 1.5:
                    status, trend = "강력 돌파", "🔥 과열이나 힘이 매우 강함"
                else:
                    status, trend = "매수(고이격)", "⚠️ 단기 과열 주의! 리스크 높음"
            else:
                status, trend = "홀드", "📈 상승 추세 유지"
        elif (prev['종가'] < prev['20MA']) and (price > ma20):
            status, trend = ("적극 매수", "⚡ 대량 거래 돌파") if vol_ratio > 1.2 else ("매수 시도", "⚓ 20일선 안착 중")
        elif price < ma20 and macd_last < macd_prev:
            status, trend = "적극 매도", "🧊 하락 추세 (지수 동반 하락시 위험)"
        
        energy = "📈 가속" if macd_last > macd_prev else "⚠️ 감속"
        chart_url = f"https://finance.naver.com/item/main.naver?code={code}"
        
        return [code, name, current_change, int(price), int(ma20), int(diff), disparity_fmt, sl_tp, status, f"{trend} | {energy}", chart_url]
    except: return None

def show_styled_dataframe(dataframe):
    if dataframe.empty: return
    st.dataframe(
        dataframe.style.applymap(lambda x: 'color: #ef5350; font-weight: bold' if '매수' in str(x) or '돌파' in str(x) else ('color: #42a5f5' if '매도' in str(x) else ''), subset=['상태'])
        .applymap(lambda x: 'color: #ef5350' if '+' in str(x) else ('color: #42a5f5' if '-' in str(x) else ''), subset=['등락률', '이격률']),
        use_container_width=True,
        column_config={"차트": st.column_config.LinkColumn("차트", display_text="열기"), "코드": st.column_config.TextColumn("코드", width="small")},
        hide_index=True
    )

# -------------------------
# UI 부분
# -------------------------
st.title("🛡️ 스마트 리스크 스캐너")

st.sidebar.header("설정")
market = st.sidebar.radio("시장 선택", ["KOSPI", "KOSDAQ"])
selected_pages = st.sidebar.multiselect("분석 페이지 선택", options=list(range(1, 41)), default=[1])
start_btn = st.sidebar.button("🚀 분석 시작")

st.subheader("📊 진단 및 필터링")
c1, c2, c3 = st.columns(3)
total_metric, buy_metric, sell_metric = c1.empty(), c2.empty(), c3.empty()
total_metric.metric("전체 종목", "0개"); buy_metric.metric("매수/돌파 신호", "0개"); sell_metric.metric("매도 신호", "0개")

col1, col2, col3 = st.columns(3)
if 'filter' not in st.session_state: st.session_state.filter = "전체"
if col1.button("🔄 전체", use_container_width=True): st.session_state.filter = "전체"
if col2.button("🔴 매수/돌파", use_container_width=True): st.session_state.filter = "매수"
if col3.button("🔵 매도", use_container_width=True): st.session_state.filter = "매도"

main_result_area = st.empty()

if start_btn:
    market_df = get_market_sum_pages(selected_pages, market)
    if not market_df.empty:
        results = []
        progress_bar = st.progress(0)
        for i, (idx, row) in enumerate(market_df.iterrows()):
            res = analyze_stock(row['종목코드'], row['종목명'], row['등락률'])
            if res:
                results.append(res)
                df_all = pd.DataFrame(results, columns=['코드', '종목명', '등락률', '현재가', '20MA', '차이', '이격률', '손절/익절', '상태', '해석', '차트'])
                st.session_state['df_all'] = df_all
                total_metric.metric("전체 종목", f"{len(df_all)}개")
                buy_metric.metric("매수/돌파 신호", f"{len(df_all[df_all['상태'].str.contains('매수|돌파')])}개")
                sell_metric.metric("매도 신호", f"{len(df_all[df_all['상태'].str.contains('매도')])}개")
                with main_result_area: show_styled_dataframe(df_all)
            progress_bar.progress((i + 1) / len(market_df))
        st.success("✅ 분석 완료!")

if 'df_all' in st.session_state:
    df = st.session_state['df_all']
    display_df = df.copy()
    if st.session_state.filter == "매수": display_df = df[df['상태'].str.contains("매수|돌파")]
    elif st.session_state.filter == "매도": display_df = df[df['상태'].str.contains("매도")]
    with main_result_area: show_styled_dataframe(display_df)
    
    email_summary = display_df[['종목명', '현재가', '이격률', '상태', '해석']].to_string(index=False)
    encoded_body = urllib.parse.quote(f"주식 분석 리포트\n\n{email_summary}")
    st.markdown(f'<a href="mailto:?subject=주식분석_리포트&body={encoded_body}" target="_self" style="text-decoration:none;"><div style="background-color:#0078d4;color:white;padding:15px;border-radius:8px;text-align:center;font-weight:bold;">📧 Outlook 전송</div></a>', unsafe_allow_html=True)
else:
    with main_result_area: st.info("분석 시작을 눌러주세요.")

