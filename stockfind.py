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

def get_price_data(code, max_pages=30): # 데이터 충분히 가져오기
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

# [핵심 수정] 일목균형표 분석 로직으로 전면 교체
def analyze_stock(code, name, current_change):
    try:
        df_daily = get_price_data(code)
        # 일목균형표는 최소 52일 데이터가 필요
        if df_daily is None or len(df_daily) < 52:
            return None

        # --- 일목균형표 분석 함수 ---
        def get_ichimoku_status(df):
            if len(df) < 52:
                return "- (데이터 부족)"

            # 1. 전환선 (Tenkan-sen): (9일 고가 + 9일 저가) / 2
            high_9 = df['고가'].rolling(9).max()
            low_9 = df['저가'].rolling(9).min()
            df['tenkan_sen'] = (high_9 + low_9) / 2

            # 2. 기준선 (Kijun-sen): (26일 고가 + 26일 저가) / 2
            high_26 = df['고가'].rolling(26).max()
            low_26 = df['저가'].rolling(26).min()
            df['kijun_sen'] = (high_26 + low_26) / 2

            # 3. 선행스팬 A (Senkou Span A): (전환선 + 기준선) / 2, 26일 미래로 이동
            df['senkou_a'] = ((df['tenkan_sen'] + df['kijun_sen']) / 2).shift(26)

            # 4. 선행스팬 B (Senkou Span B): (52일 고가 + 52일 저가) / 2, 26일 미래로 이동
            high_52 = df['고가'].rolling(52).max()
            low_52 = df['저가'].rolling(52).min()
            df['senkou_b'] = ((high_52 + low_52) / 2).shift(26)

            # 5. 후행스팬 (Chikou Span): 현재 종가를 26일 과거로 이동
            df['chikou_span'] = df['종가'].shift(-26)
            
            df = df.dropna(subset=['tenkan_sen', 'kijun_sen', 'senkou_a', 'senkou_b']).reset_index(drop=True)
            if len(df) < 2: return "- (계산 불가)"
            
            # 현재 시점의 구름대는 26일 전 데이터로 만들어짐
            last = df.iloc[-27]
            prev = df.iloc[-28]
            
            price = last['종가']
            cloud_top = max(last['senkou_a'], last['senkou_b'])
            cloud_bottom = min(last['senkou_a'], last['senkou_b'])

            prev_price = prev['종가']
            prev_cloud_top = max(prev['senkou_a'], prev['senkou_b'])
            prev_cloud_bottom = min(prev['senkou_a'], prev['senkou_b'])

            # 위치 판단
            if price > cloud_top:
                if prev_price <= prev_cloud_top: return "☁️ 구름대 상향 돌파"
                return "☀️ 구름대 위"
            elif price < cloud_bottom:
                if prev_price >= prev_cloud_bottom: return "⛈️ 구름대 하향 이탈"
                return "💧 구름대 아래"
            else:
                return "🌫️ 구름대 진입"

        # 1. 일봉 기준 일목균형표 상태 분석
        ichimoku_daily = get_ichimoku_status(df_daily.copy())

        # 2. 주봉 기준 일목균형표 상태 분석
        df_weekly = df_daily.set_index('날짜').resample('W-Fri').agg(
            {'종가': 'last', '고가': 'max', '저가': 'min', '거래량': 'sum'}
        ).reset_index()
        ichimoku_weekly = get_ichimoku_status(df_weekly.copy())

        # 3. 일봉 기준 MACD 상태 분석 (기존 유지)
        df_daily['20MA'] = df_daily['종가'].rolling(20).mean()
        ema12 = df_daily['종가'].ewm(span=12, adjust=False).mean()
        ema26 = df_daily['종가'].ewm(span=26, adjust=False).mean()
        df_daily['MACD'] = ema12 - ema26
        df_daily['MACD_Signal'] = df_daily['MACD'].ewm(span=9, adjust=False).mean()
        df_daily['MACD_hist'] = df_daily['MACD'] - df_daily['MACD_Signal']
        df_daily.dropna(inplace=True)

        if len(df_daily) < 6: return None

        last = df_daily.iloc[-1]
        prev = df_daily.iloc[-2]
        
        status, trend = "관망", "🌊 방향 탐색"
        price, ma20, macd_hist_last, macd_hist_prev, prev_price, prev_ma20 = last['종가'], last['20MA'], last['MACD_hist'], prev['MACD_hist'], prev['종가'], prev['20MA']
        disparity = ((price / ma20) - 1) * 100 if ma20 > 0 else 0
        disparity_fmt = f"{'+' if disparity >= 0 else ''}{round(disparity, 2)}%"
        macd_5d_diff = np.diff(df_daily['MACD_hist'].tail(5))
        is_macd_rebounding = sum(macd_5d_diff > 0) >= 3
        is_macd_declining = sum(macd_5d_diff < 0) >= 3

        if (macd_hist_last > 0 and macd_hist_prev <= 0) or (price > ma20 and prev_price < prev_ma20):
            status, trend = "적극 매수", "🔥 엔진 점화"
        elif (macd_hist_last < 0 and macd_hist_prev >= 0) or (price < ma20 and prev_price > prev_ma20):
            status, trend = "적극 매도", "📉 추세 하락"
        # (이하 MACD 분석 생략)
            
        macd_trend_status = '📈 가속' if macd_hist_last > macd_hist_prev else '⚠️ 감속'
        chart_url = f"https://finance.naver.com/item/fchart.naver?code={code}"

        return [code, name, current_change, int(price), disparity_fmt, status, ichimoku_daily, ichimoku_weekly, f"{trend} | {macd_trend_status}", chart_url]
    except Exception:
        return None

# [수정] 컬럼명 변경 및 스타일링 함수 이름 변경
def show_styled_dataframe(dataframe):
    if dataframe.empty:
        st.write("분석된 데이터가 없습니다. 왼쪽에서 '분석 시작'을 눌러주세요.")
        return

    def style_ichimoku_column(series):
        styles = []
        for x in series:
            if '돌파' in str(x) or '위' in str(x):
                styles.append('color: #d32f2f; font-weight: bold;') # 빨간색
            elif '이탈' in str(x) or '아래' in str(x):
                styles.append('color: #1976d2; font-weight: bold;') # 파란색
            elif '진입' in str(x):
                 styles.append('color: #757575;') # 회색
            else:
                styles.append('')
        return styles
    
    dynamic_height = (len(dataframe) + 1) * 35 + 3

    st.dataframe(
        dataframe.style
        .map(lambda x: 'color: #ef5350; font-weight: bold' if '매수' in str(x) else ('color: #42a5f5' if '매도' in str(x) else ''), subset=['상태'])
        .map(lambda x: 'color: #ef5350' if '+' in str(x) else ('color: #42a5f5' if '-' in str(x) else ''), subset=['등률', '이격률'])
        .apply(style_ichimoku_column, subset=['일목(일봉)', '일목(주봉)']),
        use_container_width=True,
        height=dynamic_height,
        column_config={"차트": st.column_config.LinkColumn("차트", display_text="열기"), "코드": st.column_config.TextColumn("코드", width="small")},
        hide_index=True
    )

# --- UI 부분 ---
st.title("🛡️ 스마트 데이터 스캐너")
st.sidebar.header("설정")
market = st.sidebar.radio("시장 선택", ["KOSPI", "KOSDAQ"])
selected_pages = st.sidebar.multiselect("분석 페이지 선택", options=list(range(1, 41)), default=[1])
start_btn = st.sidebar.button("🚀 분석 시작")
# (이하 UI 코드 기존과 동일)
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
                # [수정] 컬럼명 변경: 'BB' -> '일목'
                df_all = pd.DataFrame(results, columns=['코드', '종목명', '등락률', '현재가', '이격률', '상태', '일목(일봉)', '일목(주봉)', '해석', '차트'])
                st.session_state['df_all'] = df_all
                # (이하 코드 생략)
                total_metric.metric("전체 종목", f"{len(df_all)}개")
                buy_metric.metric("매수 신호", f"{len(df_all[df_all['상태'].str.contains('매수')])}개")
                sell_metric.metric("매도 신호", f"{len(df_all[df_all['상태'].str.contains('매도')])}개")
                with main_result_area:
                    show_styled_dataframe(df_all)
            progress_bar.progress((i + 1) / len(market_df))
        st.success("✅ 분석 완료!")
# (이하 코드 기존과 동일)
if 'df_all' in st.session_state:
    df = st.session_state['df_all']
    display_df = df.copy()
    if st.session_state.filter == "매수": display_df = df[df['상태'].str.contains("매수")]
    elif st.session_state.filter == "매도": display_df = df[df['상태'].str.contains("매도")]
    with main_result_area:
        show_styled_dataframe(display_df)
    email_summary = display_df[['종목명', '현재가', '상태', '일목(일봉)', '일목(주봉)']].to_string(index=False)
    encoded_body = urllib.parse.quote(f"주식 분석 리포트\n\n{email_summary}")
    mailto_url = f"mailto:?subject=주식리포트&body={encoded_body}"
    st.markdown(f'<a href="{mailto_url}" target="_self" style="text-decoration:none;"><div style="background-color:#0078d4;color:white;padding:15px;border-radius:8px;text-align:center;font-weight:bold;">📧 리스트 Outlook 전송</div></a>', unsafe_allow_html=True)
else:
    with main_result_area:
        st.info("사이드바에서 '분석 시작' 버튼을 눌러주세요")
