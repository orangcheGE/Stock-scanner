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
st.set_page_config(page_title="20일선 스마트 대시보드", layout="wide")

def get_headers():
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://finance.naver.com/'
    }

# --- 분석 로직 (수정 없음) ---
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
                    codes.append(match.group(1))
                    names.append(a.get_text(strip=True))
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
        if col in df.columns:
            df[col] = pd.to_numeric(df[col].astype(str).str.replace(',',''), errors='coerce')
    df['날짜'] = pd.to_datetime(df['날짜'], errors='coerce')
    return df.dropna(subset=['날짜','종가']).sort_values('날짜').reset_index(drop=True)

def analyze_stock(code, name, current_change):
    try:
        df = get_price_data(code)
        
        if df is None or len(df) < 35: 
            return None
        
        df['20MA'] = df['종가'].rolling(20).mean()
        ema12 = df['종가'].ewm(span=12, adjust=False).mean()
        ema26 = df['종가'].ewm(span=26, adjust=False).mean()
        df['MACD'] = ema12 - ema26
        df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
        df['MACD_hist'] = df['MACD'] - df['MACD_Signal']
        
        df.dropna(inplace=True)
        if len(df) < 6:
             return None

        last = df.iloc[-1]
        prev = df.iloc[-2]
        
        price = last['종가']
        ma20 = last['20MA']
        macd_hist_last = last['MACD_hist']
        macd_hist_prev = prev['MACD_hist']
        prev_price = prev['종가']
        prev_ma20 = prev['20MA']
        
        disparity = ((price / ma20) - 1) * 100 if ma20 > 0 else 0
        disparity_fmt = f"{'+' if disparity >= 0 else ''}{round(disparity, 2)}%"
        
        status, trend = "관망", "🌊 방향 탐색"
        macd_5d_diff = np.diff(df['MACD_hist'].tail(5))
        is_macd_rebounding = sum(macd_5d_diff > 0) >= 3
        is_macd_declining = sum(macd_5d_diff < 0) >= 3

        if (macd_hist_last > 0 and macd_hist_prev <= 0) or \
           (price > ma20 and prev_price < prev_ma20):
            status, trend = "적극 매수", "🔥 엔진 점화 (강력 매수 신호)"
        elif (macd_hist_last < 0 and macd_hist_prev >= 0) or \
             (price < ma20 and prev_price > prev_ma20):
            status, trend = "적극 매도", "📉 추세 하락 전환"
        elif macd_hist_last < 0 and is_macd_rebounding and price < ma20:
             status, trend = "매수 관심", "⚓️ 반등 준비 중"
        elif macd_hist_last > 0 and is_macd_declining and price > ma20:
            status, trend = "매도 관심", "⚠️ 상승 탄력 둔화"
        elif price > ma20:
            status, trend = "홀드", "📈 상승 추세 유지"
        elif price < ma20:
            status, trend = "관망", "🧊 하락 또는 횡보"
            
        macd_trend_status = '📈 가속' if macd_hist_last > macd_hist_prev else '⚠️ 감속'
        chart_url = f"https://finance.naver.com/item/fchart.naver?code={code}"
        
        return [code, name, current_change, int(price), disparity_fmt, status, f"{trend} | {macd_trend_status}", chart_url]
    
    except Exception:
        return None

def show_styled_dataframe(dataframe):
    # 이 함수는 이제 비어있지 않은 DataFrame만 받게 되므로 empty 체크 간소화
    st.dataframe(
        dataframe.style.applymap(lambda x: 'color: #ef5350; font-weight: bold' if '매수' in str(x) else ('color: #42a5f5' if '매도' in str(x) else ''), subset=['상태'])
        .applymap(lambda x: 'color: #ef5350' if '+' in str(x) else ('color: #42a5f5' if '-' in str(x) else ''), subset=['등락률', '이격률']),
        use_container_width=True,
        column_config={"차트": st.column_config.LinkColumn("차트", display_text="열기"), "코드": st.column_config.TextColumn("코드", width="small")},
        hide_index=True
    )

# -------------------------
# UI 및 제어 로직 (수정된 부분)
# -------------------------

st.title("🛡️ 20일선 스마트 데이터 스캐너")

st.sidebar.header("설정")
market = st.sidebar.radio("시장 선택", ["KOSPI", "KOSDAQ"])
selected_pages = st.sidebar.multiselect("분석 페이지 선택", options=list(range(1, 41)), default=[1])
start_btn = st.sidebar.button("🚀 분석 시작")

st.subheader("📊 진단 및 필터링")
c1, c2, c3 = st.columns(3)
total_metric = c1.empty()
buy_metric = c2.empty()
sell_metric = c3.empty()

# 필터 버튼
col1, col2, col3 = st.columns(3)
if 'filter' not in st.session_state: st.session_state.filter = "전체"
if col1.button("🔄 전체 보기", use_container_width=True): st.session_state.filter = "전체"
if col2.button("🔴 매수 관련만", use_container_width=True): st.session_state.filter = "매수"
if col3.button("🔵 매도 관련만", use_container_width=True): st.session_state.filter = "매도"

st.markdown("---")
result_title = st.empty()
main_result_area = st.empty() # 결과를 표시할 중앙 공간

# --- '분석 시작' 버튼을 눌렀을 때의 로직 ---
if start_btn:
    market_df = get_market_sum_pages(selected_pages, market)
    results = [] # 분석 결과를 담을 리스트

    if not market_df.empty:
        progress_bar = st.progress(0, text="종목 분석 중...")
        total_stocks = len(market_df)
        for i, (idx, row) in enumerate(market_df.iterrows()):
            res = analyze_stock(row['종목코드'], row['종목명'], row['등락률'])
            if res:
                results.append(res)
            progress_bar.progress((i + 1) / total_stocks, text=f"종목 분석 중... ({i+1}/{total_stocks})")
    
    # 분석이 모두 끝난 후, 결과를 DataFrame으로 만들고 session_state에 저장
    if results:
        df_all = pd.DataFrame(results, columns=['코드', '종목명', '등락률', '현재가', '이격률', '상태', '해석', '차트'])
        st.session_state['df_all'] = df_all
        st.success("✅ 분석 완료!")
    else:
        # 분석 결과가 없으면 기존 session_state 데이터도 삭제
        if 'df_all' in st.session_state:
            del st.session_state['df_all']
        st.warning("분석 조건에 맞는 종목을 찾지 못했습니다.")

# --- 화면 표시 로직 (분석/필터링 결과를 항상 여기서 그림) ---
result_title.subheader(f"🔍 결과 리스트 ({st.session_state.filter})")

if 'df_all' in st.session_state and not st.session_state['df_all'].empty:
    df = st.session_state['df_all']
    display_df = df.copy()

    # 필터링 로직
    if st.session_state.filter == "매수":
        display_df = df[df['상태'].str.contains("매수")]
    elif st.session_state.filter == "매도":
        display_df = df[df['상태'].str.contains("매도")]

    # 메트릭 업데이트
    total_metric.metric("전체 종목", f"{len(df)}개")
    buy_metric.metric("매수 신호", f"{len(df[df['상태'].str.contains('매수')])}개")
    sell_metric.metric("매도 신호", f"{len(df[df['상태'].str.contains('매도')])}개")

    # 중앙 영역에 데이터프레임 표시
    with main_result_area:
        if not display_df.empty:
            show_styled_dataframe(display_df)
        else:
            # 필터링 결과 데이터가 없을 때
            st.info(f"'{st.session_state.filter}' 조건에 맞는 데이터가 없습니다.")

    # 이메일 전송 링크 (필터링된 결과가 있을 때만 표시)
    if not display_df.empty:
        email_summary = display_df[['종목명', '현재가', '상태']].to_string(index=False)
        encoded_body = urllib.parse.quote(f"주식 분석 리포트\n\n{email_summary}")
        mailto_url = f"mailto:?subject=주식리포트&body={encoded_body}"
        st.markdown(f'<a href="{mailto_url}" target="_self" style="text-decoration:none;"><div style="background-color:#0078d4;color:white;padding:15px;border-radius:8px;text-align:center;font-weight:bold;">📧 리스트 Outlook 전송</div></a>', unsafe_allow_html=True)

else:
    # 앱 초기 상태 또는 분석 결과가 없을 때
    total_metric.metric("전체 종목", "0개")
    buy_metric.metric("매수 신호", "0개")
    sell_metric.metric("매도 신호", "0개")
    with main_result_area:
        st.info("사이드바에서 '분석 시작' 버튼을 눌러주세요")
