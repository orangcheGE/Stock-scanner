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

# --- 분석 로직 ---
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
        
        # 1. 데이터 유효성 검사 (20MA, MACD(26), 5일 추세 분석을 위해 최소 35일 데이터 권장)
        if df is None or len(df) < 35: 
            return None

        # --- 지표 계산 ---
        df['20MA'] = df['종가'].rolling(20).mean()
        ema12 = df['종가'].ewm(span=12, adjust=False).mean()
        ema26 = df['종가'].ewm(span=26, adjust=False).mean()
        df['MACD'] = ema12 - ema26
        df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
        df['MACD_hist'] = df['MACD'] - df['MACD_Signal']
        
        # 지표 계산 후 발생한 NaN 데이터 제거
        df.dropna(inplace=True)
        if len(df) < 6: # 5일치 추세 비교를 위해 최소 6일의 데이터 필요
             return None

        # --- 분석을 위한 데이터 준비 ---
        last = df.iloc[-1]
        prev = df.iloc[-2]
        
        price = last['종가']
        ma20 = last['20MA']
        macd_hist_last = last['MACD_hist']
        macd_hist_prev = prev['MACD_hist']

        prev_price = prev['종가']
        prev_ma20 = prev['20MA']
        
        # 이격률 계산
        disparity = ((price / ma20) - 1) * 100 if ma20 > 0 else 0
        disparity_fmt = f"{'+' if disparity >= 0 else ''}{round(disparity, 2)}%"

        # --- 새로운 분석 로직 ---
        status, trend = "관망", "🌊 방향 탐색" # 기본값 설정

        # 5일간 MACD 히스토그램 추세 분석 (최근 5일 중 3일 이상 상승/하락했는지)
        macd_5d_diff = np.diff(df['MACD_hist'].tail(5))
        is_macd_rebounding = sum(macd_5d_diff > 0) >= 3
        is_macd_declining = sum(macd_5d_diff < 0) >= 3

        # --- 조건 평가 (우선순위가 높은 순서대로) ---

        # 1. '적극 매수' (Strong Buy)
        #    - MACD 히스토그램이 0을 상향 돌파했거나, 주가가 20MA를 골든 크로스한 경우
        if (macd_hist_last > 0 and macd_hist_prev <= 0) or \
           (price > ma20 and prev_price < prev_ma20):
            status, trend = "적극 매수", "🔥 엔진 점화 (강력 매수 신호)"

        # 2. '적극 매도' (Strong Sell)
        #    - MACD 히스토그램이 0을 하향 돌파했거나, 주가가 20MA를 데드 크로스한 경우
        elif (macd_hist_last < 0 and macd_hist_prev >= 0) or \
             (price < ma20 and prev_price > prev_ma20):
            status, trend = "적극 매도", "📉 추세 하락 전환"
        
        # 3. '매수 관심' (Buy Interest)
        #    - MACD 히스토그램이 음수(-) 영역에 있고, 지난 5일간 상승 전환 추세이며,
        #    - 주가가 20MA를 향해 아래에서 접근하는 경우
        elif macd_hist_last < 0 and is_macd_rebounding and price < ma20:
             status, trend = "매수 관심", "⚓️ 반등 준비 중"
        
        # 4. '매도 관심' (Sell Interest)
        #    - MACD 히스토그램이 양수(+) 영역에 있고, 지난 5일간 하락 전환 추세이며,
        #    - 주가가 20MA를 향해 위에서 접근하는 경우
        elif macd_hist_last > 0 and is_macd_declining and price > ma20:
            status, trend = "매도 관심", "⚠️ 상승 탄력 둔화"

        # 5. 기타 추세 지속 구간
        elif price > ma20: # 20MA 위에 있을 때
            status, trend = "홀드", "📈 상승 추세 유지"
        elif price < ma20: # 20MA 아래에 있을 때
            status, trend = "관망", "🧊 하락 또는 횡보"

        # 최종 추세 가속/감속 판단
        macd_trend_status = '📈 가속' if macd_hist_last > macd_hist_prev else '⚠️ 감속'
        
        chart_url = f"https://finance.naver.com/item/fchart.naver?code={code}"
        
        return [code, name, current_change, int(price), disparity_fmt, status, f"{trend} | {macd_trend_status}", chart_url]
    
    except Exception:
        # 오류 발생 시 해당 종목은 건너뜁니다.
        return None

def show_styled_dataframe(dataframe):
    if dataframe.empty:
        st.write("분석된 데이터가 없습니다. 왼쪽에서 '분석 시작'을 눌러주세요.")
        return
    st.dataframe(
        dataframe.style.applymap(lambda x: 'color: #ef5350; font-weight: bold' if '매수' in str(x) else ('color: #42a5f5' if '매도' in str(x) else ''), subset=['상태'])
        .applymap(lambda x: 'color: #ef5350' if '+' in str(x) else ('color: #42a5f5' if '-' in str(x) else ''), subset=['등락률', '이격률']),
        use_container_width=True,
        column_config={"차트": st.column_config.LinkColumn("차트", display_text="열기"), "코드": st.column_config.TextColumn("코드", width="small")},
        hide_index=True
    )

# -------------------------
# UI 부분
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

total_metric.metric("전체 종목", "0개")
buy_metric.metric("매수 신호", "0개")
sell_metric.metric("매도 신호", "0개")

col1, col2, col3 = st.columns(3)
if 'filter' not in st.session_state: st.session_state.filter = "전체"
btn_all = col1.button("🔄 전체 보기", use_container_width=True)
btn_buy = col2.button("🔴 매수 관련만", use_container_width=True)
btn_sell = col3.button("🔵 매도 관련만", use_container_width=True)

if btn_all: st.session_state.filter = "전체"
if btn_buy: st.session_state.filter = "매수"
if btn_sell: st.session_state.filter = "매도"

st.markdown("---")
result_title = st.empty()
result_title.subheader(f"🔍 결과 리스트 ({st.session_state.filter})")
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
                # 컬럼명 리스트에서 '20MA', '차이', '손절/익절' 제거
                df_all = pd.DataFrame(results, columns=['코드', '종목명', '등락률', '현재가', '이격률', '상태', '해석', '차트'])
                st.session_state['df_all'] = df_all
                
                total_metric.metric("전체 종목", f"{len(df_all)}개")
                buy_metric.metric("매수 신호", f"{len(df_all[df_all['상태'].str.contains('매수')])}개")
                sell_metric.metric("매도 신호", f"{len(df_all[df_all['상태'].str.contains('매도')])}개")
                
                with main_result_area:
                    show_styled_dataframe(df_all)
            progress_bar.progress((i + 1) / len(market_df))
        st.success("✅ 분석 완료!")

if 'df_all' in st.session_state:
    df = st.session_state['df_all']
    display_df = df.copy()
    if st.session_state.filter == "매수": display_df = df[df['상태'].str.contains("매수")]
    elif st.session_state.filter == "매도": display_df = df[df['상태'].str.contains("매도")]
    
    with main_result_area:
        show_styled_dataframe(display_df)
        
    email_summary = display_df[['종목명', '현재가', '상태']].to_string(index=False)
    encoded_body = urllib.parse.quote(f"주식 분석 리포트\n\n{email_summary}")
    mailto_url = f"mailto:?subject=주식리포트&body={encoded_body}"
    st.markdown(f'<a href="{mailto_url}" target="_self" style="text-decoration:none;"><div style="background-color:#0078d4;color:white;padding:15px;border-radius:8px;text-align:center;font-weight:bold;">📧 리스트 Outlook 전송</div></a>', unsafe_allow_html=True)
else:
    with main_result_area:
        st.info("사이드바에서 '분석 시작' 버튼을 눌러주세요.")



