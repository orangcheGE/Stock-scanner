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

# (get_headers, get_market_sum_pages, get_price_data 함수는 이전과 동일)
def get_headers():
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://finance.naver.com/'
    }

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

def get_price_data(code, max_pages=25):
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


# [수정] 볼린저 밴드 상태를 별도 항목으로 반환하도록 수정
def analyze_stock(code, name, current_change, timeframe='일봉'):
    try:
        df_daily = get_price_data(code)

        if timeframe == '주봉':
            df_daily = df_daily.set_index('날짜')
            df = df_daily.resample('W').agg({'종가': 'last', '고가': 'max', '저가': 'min', '거래량': 'sum'}).reset_index()
            if df is None or len(df) < 35: return None
        else:
            df = df_daily
            if df is None or len(df) < 35: return None

        df['20MA'] = df['종가'].rolling(20).mean()
        df['20STD'] = df['종가'].rolling(20).std()
        df['UpperBand'] = df['20MA'] + (df['20STD'] * 2)
        df['LowerBand'] = df['20MA'] - (df['20STD'] * 2)
        
        # (MACD 등 기타 지표 계산은 동일)
        ema12 = df['종가'].ewm(span=12, adjust=False).mean()
        ema26 = df['종가'].ewm(span=26, adjust=False).mean()
        df['MACD'] = ema12 - ema26
        df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
        df['MACD_hist'] = df['MACD'] - df['MACD_Signal']

        df.dropna(inplace=True)
        if len(df) < 6: return None

        last = df.iloc[-1]
        prev = df.iloc[-2]

        price = last['종가']
        upper_band = last['UpperBand']
        lower_band = last['LowerBand']

        # [수정] 볼린저 밴드 상태를 독립된 텍스트로 생성
        bollinger_status = "🌊 밴드 내"
        if price > upper_band:
            bollinger_status = "🔥 상단 돌파"
        elif price < lower_band:
            bollinger_status = "💧 하단 이탈"
            
        # (기존 상태 분석 로직은 동일)
        status, trend = "관망", "🌊 방향 탐색"
        ma20, macd_hist_last, macd_hist_prev, prev_price, prev_ma20 = last['20MA'], last['MACD_hist'], prev['MACD_hist'], prev['종가'], prev['20MA']
        disparity = ((price / ma20) - 1) * 100 if ma20 > 0 else 0
        disparity_fmt = f"{'+' if disparity >= 0 else ''}{round(disparity, 2)}%"
        macd_5d_diff = np.diff(df['MACD_hist'].tail(5))
        is_macd_rebounding = sum(macd_5d_diff > 0) >= 3
        is_macd_declining = sum(macd_5d_diff < 0) >= 3

        if (macd_hist_last > 0 and macd_hist_prev <= 0) or (price > ma20 and prev_price < prev_ma20):
            status, trend = "적극 매수", "🔥 엔진 점화"
        elif (macd_hist_last < 0 and macd_hist_prev >= 0) or (price < ma20 and prev_price > prev_ma20):
            status, trend = "적극 매도", "📉 추세 하락"
        elif macd_hist_last < 0 and is_macd_rebounding and price < ma20:
             status, trend = "매수 관심", "⚓️ 반등 준비"
        elif macd_hist_last > 0 and is_macd_declining and price > ma20:
            status, trend = "매도 관심", "⚠️ 탄력 둔화"
        elif price > ma20:
            status, trend = "홀드", "📈 상승 유지"
        elif price < ma20:
            status, trend = "관망", "🧊 하락/횡보"
        
        macd_trend_status = '📈 가속' if macd_hist_last > macd_hist_prev else '⚠️ 감속'
        chart_url = f"https://finance.naver.com/item/fchart.naver?code={code}"
        
        # [수정] 반환 리스트에 'bollinger_status'를 별도 항목으로 추가
        return [code, name, current_change, int(price), disparity_fmt, status, bollinger_status, f"{trend} | {macd_trend_status}", chart_url]
    
    except Exception:
        return None

# [수정] 'BB 위치' 컬럼에 대한 스타일링 추가
def show_styled_dataframe(dataframe):
    if dataframe.empty:
        st.write("분석된 데이터가 없습니다. 왼쪽에서 '분석 시작'을 눌러주세요.")
        return
        
    st.dataframe(
        dataframe.style.map(lambda x: 'color: #ef5350; font-weight: bold' if '매수' in str(x) else ('color: #42a5f5' if '매도' in str(x) else ''), subset=['상태'])
        .map(lambda x: 'color: #ef5350' if '+' in str(x) else ('color: #42a5f5' if '-' in str(x) else ''), subset=['등락률', '이격률'])
        .map(lambda x: 'color: white; background-color: #d32f2f; font-weight: bold;' if '돌파' in str(x) else ('color: white; background-color: #1976d2; font-weight: bold;' if '이탈' in str(x) else ''), subset=['BB 위치']),
        use_container_width=True,
        column_config={"차트": st.column_config.LinkColumn("차트", display_text="열기"), "코드": st.column_config.TextColumn("코드", width="small")},
        hide_index=True
    )

# --- UI 부분 ---
st.title("🛡️ 20일선 스마트 데이터 스캐너")
st.sidebar.header("설정")

market = st.sidebar.radio("시장 선택", ["KOSPI", "KOSDAQ"])
timeframe = st.sidebar.radio("분석 기준", ["일봉", "주봉"])
selected_pages = st.sidebar.multiselect("분석 페이지 선택", options=list(range(1, 41)), default=[1])
start_btn = st.sidebar.button("🚀 분석 시작")

# (중간 UI 부분은 동일)
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
result_title.subheader(f"🔍 {timeframe} 기준 결과 리스트 ({st.session_state.filter})")
main_result_area = st.empty()


if start_btn:
    market_df = get_market_sum_pages(selected_pages, market)
    if not market_df.empty:
        results = []
        progress_bar = st.progress(0)
        for i, (idx, row) in enumerate(market_df.iterrows()):
            res = analyze_stock(row['종목코드'], row['종목명'], row['등락률'], timeframe)
            if res:
                results.append(res)
                # [수정] 컬럼 리스트에 'BB 위치' 추가
                df_all = pd.DataFrame(results, columns=['코드', '종목명', '등락률', '현재가', '이격률', '상태', 'BB 위치', '해석', '차트'])
                st.session_state['df_all'] = df_all
                
                total_metric.metric("전체 종목", f"{len(df_all)}개")
                buy_metric.metric("매수 신호", f"{len(df_all[df_all['상태'].str.contains('매수')])}개")
                sell_metric.metric("매도 신호", f"{len(df_all[df_all['상태'].str.contains('매도')])}개")
                
                with main_result_area:
                    show_styled_dataframe(df_all)
            progress_bar.progress((i + 1) / len(market_df))
        st.success("✅ 분석 완료!")

# (나머지 부분은 동일)
if 'df_all' in st.session_state:
    df = st.session_state['df_all']
    display_df = df.copy()
    if st.session_state.filter == "매수": display_df = df[df['상태'].str.contains("매수")]
    elif st.session_state.filter == "매도": display_df = df[df['상태'].str.contains("매도")]
    
    with main_result_area:
        show_styled_dataframe(display_df)
        
    email_summary = display_df[['종목명', '현재가', '상태', 'BB 위치']].to_string(index=False)
    encoded_body = urllib.parse.quote(f"주식 분석 리포트 ({timeframe} 기준)\n\n{email_summary}")
    mailto_url = f"mailto:?subject=주식리포트&body={encoded_body}"
    st.markdown(f'<a href="{mailto_url}" target="_self" style="text-decoration:none;"><div style="background-color:#0078d4;color:white;padding:15px;border-radius:8px;text-align:center;font-weight:bold;">📧 리스트 Outlook 전송</div></a>', unsafe_allow_html=True)
else:
    with main_result_area:
        st.info("사이드바에서 '분석 시작' 버튼을 눌러주세요")

