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

# [최종 수정] 주봉 계산 오류를 바로잡은 analyze_stock 함수
# [최종 수정] 이동평균선 '크로스오버' 상태 분석 기능으로 교체
def analyze_stock(code, name, current_change):
    try:
        df_daily = get_price_data(code, max_pages=15)
        if df_daily is None or len(df_daily) < 70:
            return None

        # --- 1. 주요 이동평균선 계산 ---
        df_daily['5MA'] = df_daily['종가'].rolling(5).mean()
        df_daily['20MA'] = df_daily['종가'].rolling(20).mean()
        df_daily['60MA'] = df_daily['종가'].rolling(60).mean()

        # --- 2. 일목균형표(일봉) 계산 ---
        high_9 = df_daily['고가'].rolling(9).max()
        low_9 = df_daily['저가'].rolling(9).min()
        df_daily['tenkan_sen'] = (high_9 + low_9) / 2
        high_26 = df_daily['고가'].rolling(26).max()
        low_26 = df_daily['저가'].rolling(26).min()
        df_daily['kijun_sen'] = (high_26 + low_26) / 2
        df_daily['senkou_a'] = ((df_daily['tenkan_sen'] + df_daily['kijun_sen']) / 2).shift(25)
        high_52 = df_daily['고가'].rolling(52).max()
        low_52 = df_daily['저가'].rolling(52).min()
        df_daily['senkou_b'] = ((high_52 + low_52) / 2).shift(25)

        # --- MACD 계산 ---
        ema12 = df_daily['종가'].ewm(span=12, adjust=False).mean()
        ema26 = df_daily['종가'].ewm(span=26, adjust=False).mean()
        df_daily['MACD'] = ema12 - ema26
        df_daily['MACD_Signal'] = df_daily['MACD'].ewm(span=9, adjust=False).mean()
        df_daily['MACD_hist'] = df_daily['MACD'] - df_daily['MACD_Signal']
        
        df_daily.dropna(inplace=True)
        if len(df_daily) < 2: # 최소 2일치 데이터로 비교
            return None

        last = df_daily.iloc[-1]
        prev = df_daily.iloc[-2]

        # --- 3. [신규] 이동평균선 크로스오버 분석 함수 ---
        def get_ma_crossover_status(last_row, prev_row, ma_col):
            price_last = last_row['종가']
            ma_last = last_row[ma_col]
            price_prev = prev_row['종가']
            ma_prev = prev_row[ma_col]

            # 골든크로스
            if price_last > ma_last and price_prev <= ma_prev:
                return "🔥 골든크로스"
            # 데드크로스
            elif price_last < ma_last and price_prev >= ma_prev:
                return "🧊 데드크로스"
            # 상승 추세 유지
            elif price_last > ma_last:
                return "📈 상승 유지"
            # 하락 추세 유지
            else:
                return "📉 하락 유지"

        status_5ma = get_ma_crossover_status(last, prev, '5MA')
        status_20ma = get_ma_crossover_status(last, prev, '20MA')
        status_60ma = get_ma_crossover_status(last, prev, '60MA')
        ma_crossover_text = f"5MA: {status_5ma} | 20MA: {status_20ma} | 60MA: {status_60ma}"
        
        # --- 4. 일목균형표(일봉) 상태 분석 ---
        price = last['종가']
        cloud_top = last['senkou_a']
        cloud_bottom = last['senkou_b']
        ichimoku_status = "🌫️ 구름대 진입"
        if price > cloud_top: ichimoku_status = "☀️ 구름대 위"
        elif price < cloud_bottom: ichimoku_status = "💧 구름대 아래"
        
        # --- 5. MACD 기반 매매 신호 분석 ---
        status = "관망"
        if (last['MACD_hist'] > 0 and prev['MACD_hist'] <= 0):
            status = "적극 매수"
        elif (last['MACD_hist'] < 0 and prev['MACD_hist'] >= 0):
            status = "적극 매도"
        elif status_20ma == "🔥 골든크로스":
             status = "매수 관심"
        elif status_20ma == "🧊 데드크로스":
            status = "매도 관심"
        elif price > last['20MA']:
            status = "홀드"

        disparity = ((price / last['20MA']) - 1) * 100 if last['20MA'] > 0 else 0
        disparity_fmt = f"{'+' if disparity >= 0 else ''}{round(disparity, 2)}%"
        chart_url = f"https://finance.naver.com/item/fchart.naver?code={code}"

        return [code, name, current_change, int(price), disparity_fmt, status, ichimoku_status, ma_crossover_text, chart_url]
    except Exception:
        return None

# [수정] '등률' -> '등락률' 오타를 수정한 함수
# [최종 수정] 존재하지 않는 '일목(주봉)' 컬럼 참조 오류를 수정한 함수
def show_styled_dataframe(dataframe):
    if dataframe.empty:
        st.write("분석된 데이터가 없습니다. 왼쪽에서 '분석 시작'을 눌러주세요.")
        return

    # 'MA 크로스' 컬럼에 대한 스타일링 함수
    def style_ma_crossover(val):
        color = '#757575' # 기본 회색
        if '🔥' in val: color = '#d32f2f' # 빨간색
        elif '🧊' in val: color = '#1976d2' # 파란색
        elif '📈' in val: color = '#e57373' # 옅은 빨간색
        elif '📉' in val: color = '#64b5f6' # 옅은 파란색
        return f'color: {color}'

    dynamic_height = (len(dataframe) + 1) * 35 + 3

    st.dataframe(
        dataframe.style
        .map(lambda x: 'color: #ef5350; font-weight: bold' if '매수' in str(x) else ('color: #42a5f5' if '매도' in str(x) else ''), subset=['상태'])
        .map(lambda x: 'color: #ef5350' if '+' in str(x) else ('color: #42a5f5' if '-' in str(x) else ''), subset=['등락률', '이격률'])
        # [핵심 수정] '일목(주봉)'을 제거하고, '일목(일봉)'에만 스타일 적용
        .map(lambda x: 'color: #d32f2f; font-weight: bold;' if '위' in str(x) else ('color: #1976d2; font-weight: bold;' if '아래' in str(x) else 'color: #757575;'), subset=['일목(일봉)'])
        # [추가] 'MA 크로스' 컬럼에 새로운 스타일 적용
        .applymap(style_ma_crossover, subset=['MA 크로스']),
        use_container_width=True,
        height=dynamic_height,
        column_config={
            "차트": st.column_config.LinkColumn("차트", display_text="열기"),
            "코드": st.column_config.TextColumn("코드", width="small"),
            "MA 크로스": st.column_config.TextColumn("MA 크로스", width="large")
        },
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
                # [수정] 컬럼명을 'MA 크로스'로 변경
                df_all = pd.DataFrame(results, columns=['코드', '종목명', '등락률', '현재가', '이격률', '상태', '일목(일봉)', 'MA 크로스', '차트'])
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
    email_summary = display_df[['종목명', '현재가', '상태', '일목(일봉)', 'MA 크로스']].to_string(index=False)
    encoded_body = urllib.parse.quote(f"주식 분석 리포트\n\n{email_summary}")
    mailto_url = f"mailto:?subject=주식리포트&body={encoded_body}"
    st.markdown(f'<a href="{mailto_url}" target="_self" style="text-decoration:none;"><div style="background-color:#0078d4;color:white;padding:15px;border-radius:8px;text-align:center;font-weight:bold;">📧 리스트 Outlook 전송</div></a>', unsafe_allow_html=True)
else:
    with main_result_area:
        st.info("사이드바에서 '분석 시작' 버튼을 눌러주세요")
