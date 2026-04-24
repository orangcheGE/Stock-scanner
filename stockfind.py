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

# (get_headers, get_market_sum_pages, get_price_data, analyze_stock, show_styled_dataframe 함수는 이전과 동일)

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


def analyze_stock(code, name, current_change):
    try:
        df_price = get_price_data(code, max_pages=20) # 데이터 충분히 확보
        if df_price is None or len(df_price) < 80:
            return None

        df_present = df_price.set_index('날짜')
        df_present['5MA'] = df_present['종가'].rolling(5).mean()
        df_present['20MA'] = df_present['종가'].rolling(20).mean()
        df_present['60MA'] = df_present['종가'].rolling(60).mean()
        high_9 = df_present['고가'].rolling(9).max()
        low_9 = df_present['저가'].rolling(9).min()
        df_present['tenkan_sen'] = (high_9 + low_9) / 2
        high_26 = df_present['고가'].rolling(26).max()
        low_26 = df_present['저가'].rolling(26).min()
        df_present['kijun_sen'] = (high_26 + low_26) / 2
        high_52 = df_present['고가'].rolling(52).max()
        low_52 = df_present['저가'].rolling(52).min()
        df_present['senkou_b_base'] = (high_52 + low_52) / 2
        ema12 = df_present['종가'].ewm(span=12, adjust=False).mean()
        ema26 = df_present['종가'].ewm(span=26, adjust=False).mean()
        df_present['MACD'] = ema12 - ema26
        df_present['MACD_Signal'] = df_present['MACD'].ewm(span=9, adjust=False).mean()
        df_present['MACD_hist'] = df_present['MACD'] - df_present['MACD_Signal']
        
        df_future = pd.DataFrame(index=df_present.index)
        df_future['senkou_a'] = (df_present['tenkan_sen'] + df_present['kijun_sen']) / 2
        df_future['senkou_b'] = df_present['senkou_b_base']
        df_future.index = df_future.index + pd.DateOffset(days=26)
        
        df_merged = pd.merge(df_present, df_future, left_index=True, right_index=True, how='left')
        
        df_final = df_merged.dropna().copy()
        if len(df_final) < 4: return None

        last = df_final.iloc[-1]
        prev = df_final.iloc[-2]
        
        price_today = last['종가']
        cloud_top_today = max(last['senkou_a'], last['senkou_b'])
        cloud_bottom_today = min(last['senkou_a'], last['senkou_b'])
        price_yesterday = prev['종가']
        cloud_top_yesterday = max(prev['senkou_a'], prev['senkou_b'])
        
        ichimoku_status = "🌫️ 구름대 진입"
        if price_today > cloud_top_today:
            if price_yesterday <= cloud_top_yesterday:
                ichimoku_status = "🔥 최근 상향돌파"
            else:
                ichimoku_status = "📈 구름대 위"
        elif price_today < cloud_bottom_today:
            if price_yesterday >= cloud_top_yesterday:
                ichimoku_status = "🧊 최근 하향이탈"
            else:
                ichimoku_status = "📉 구름대 아래"
        
        def get_ma_crossover_status(last_row, prev_row, ma_col):
            price_last, ma_last = last_row['종가'], last_row[ma_col]
            price_prev, ma_prev = prev_row['종가'], prev_row[ma_col]
            is_golden_cross = (price_prev <= ma_prev) and (price_last > ma_last)
            is_dead_cross = (price_prev >= ma_prev) and (price_last < ma_last)

            if is_golden_cross: return "🔥 골든크로스"
            if is_dead_cross: return "🧊 데드크로스"
            if price_last > ma_last: return "📈 상승 유지"
            else: return "📉 하락 유지"

        status_5ma = get_ma_crossover_status(last, prev, '5MA')
        status_20ma = get_ma_crossover_status(last, prev, '20MA')
        status_60ma = get_ma_crossover_status(last, prev, '60MA')
        ma_crossover_text = f"5MA: {status_5ma} | 20MA: {status_20ma} | 60MA: {status_60ma}"
        
        status = "관망"
        if (last['MACD_hist'] > 0 and prev['MACD_hist'] <= 0): status = "적극 매수"
        elif (last['MACD_hist'] < 0 and prev['MACD_hist'] >= 0): status = "적극 매도"
        elif ichimoku_status == '🔥 최근 상향돌파': status = "매수 관심"
        elif ichimoku_status == '🧊 최근 하향이탈': status = "매도 관심"
        elif last['종가'] > last['20MA']: status = "홀드"

        disparity = ((last['종가'] / last['20MA']) - 1) * 100 if last['20MA'] > 0 else 0
        disparity_fmt = f"{'+' if disparity >= 0 else ''}{round(disparity, 2)}%"
        chart_url = f"https://finance.naver.com/item/fchart.naver?code={code}"

        return [code, name, current_change, int(last['종가']), disparity_fmt, status, ichimoku_status, ma_crossover_text, chart_url]
    except Exception as e:
        return None

def show_styled_dataframe(dataframe):
    if dataframe.empty:
        st.write("분석된 데이터가 없습니다. 왼쪽에서 '분석 시작'을 눌러주세요.")
        return

    def style_ichimoku_column(val):
        if '🔥 최근 상향돌파' in val: return 'color: white; background-color: #c62828; font-weight: bold;'
        if '🧊 최근 하향이탈' in val: return 'color: white; background-color: #1565c0; font-weight: bold;'
        if '📈 구름대 위' in val: return 'color: #d32f2f;'
        if '📉 구름대 아래' in val: return 'color: #1976d2;'
        if '🌫️ 구름대 진입' in val: return 'color: #757575;'
        return ''

    def style_ma_crossover(val):
        color, weight = '#757575', 'normal'
        if '🔥' in val: color, weight = '#d32f2f', 'bold'
        elif '🧊' in val: color, weight = '#1976d2', 'bold'
        elif '📈' in val: color = '#ef5350'
        elif '📉' in val: color = '#64b5f6'
        return f'color: {color}; font-weight: {weight};'

    dynamic_height = (len(dataframe) + 1) * 35 + 3
    st.dataframe(
        dataframe.style
        .map(lambda x: 'color: #ef5350; font-weight: bold' if '매수' in str(x) else ('color: #42a5f5' if '매도' in str(x) else ''), subset=['상태'])
        .map(lambda x: 'color: #ef5350' if '+' in str(x) else ('color: #42a5f5' if '-' in str(x) else ''), subset=['등락률', '이격률'])
        .map(style_ichimoku_column, subset=['일목(일봉)'])
        .map(style_ma_crossover, subset=['MA 크로스']),
        use_container_width=True,
        height=dynamic_height,
        column_config={ "차트": st.column_config.LinkColumn("차트", display_text="열기"), "코드": st.column_config.TextColumn("코드", width="small"), "MA 크로스": st.column_config.TextColumn("MA 크로스", width="large") },
        hide_index=True
    )

# --- UI 부분 ---
st.title("🛡️ 스마트 데이터 스캐너")
st.sidebar.header("설정")
market = st.sidebar.radio("시장 선택", ["KOSPI", "KOSDAQ"])
selected_pages = st.sidebar.multiselect("분석 페이지 선택", options=list(range(1, 41)), default=[1])
start_btn = st.sidebar.button("🚀 분석 시작")

st.subheader("📊 진단 및 필터링")
c1, c2, c3, c4 = st.columns(4)
total_metric = c1.empty()
buy_metric = c2.empty()
sell_metric = c3.empty()
cloud_metric = c4.empty()

total_metric.metric("전체 종목", "0개")
buy_metric.metric("매수 관련", "0개")
sell_metric.metric("매도 관련", "0개")
cloud_metric.metric("구름대 관련", "0개")

col1, col2, col3, col4 = st.columns(4)
if 'filter' not in st.session_state: st.session_state.filter = "전체"
btn_all = col1.button("🔄 전체 보기", use_container_width=True)
btn_buy = col2.button("🔴 매수 관련만", use_container_width=True)
btn_sell = col3.button("🔵 매도 관련만", use_container_width=True)
btn_cloud = col4.button("☁️ 구름대 관련만", use_container_width=True)
if btn_all: st.session_state.filter = "전체"
if btn_buy: st.session_state.filter = "매수"
if btn_sell: st.session_state.filter = "매도"
if btn_cloud: st.session_state.filter = "구름대"

st.markdown("---")
result_title = st.empty()
main_result_area = st.empty()

# [수정] '분석 시작' 버튼 로직
if start_btn:
    # 분석 시작 시 필터를 '전체'로 초기화
    st.session_state.filter = "전체"
    market_df = get_market_sum_pages(selected_pages, market)
    
    if not market_df.empty:
        results = []
        st.session_state['df_all'] = pd.DataFrame() # 결과 초기화
        progress_bar = st.progress(0, text="분석 시작...")
        
        for i, (idx, row) in enumerate(market_df.iterrows()):
            res = analyze_stock(row['종목코드'], row['종목명'], row['등락률'])
            if res:
                results.append(res)
                df_all = pd.DataFrame(results, columns=['코드', '종목명', '등락률', '현재가', '이격률', '상태', '일목(일봉)', 'MA 크로스', '차트'])
                st.session_state['df_all'] = df_all

                # 메트릭 업데이트
                total_metric.metric("전체 종목", f"{len(df_all)}개")
                buy_metric.metric("매수 관련", f"{len(df_all[df_all['상태'].str.contains('매수')])}개")
                sell_metric.metric("매도 관련", f"{len(df_all[df_all['상태'].str.contains('매도')])}개")
                cloud_metric.metric("구름대 관련", f"{len(df_all[df_all['일목(일봉)'].str.contains('돌파|이탈|진입')])}개")
                
                # [핵심 수정] 결과를 루프 안에서 즉시 표시
                result_title.subheader(f"🔍 결과 리스트 ({st.session_state.filter} / {len(df_all)}개)")
                with main_result_area:
                    show_styled_dataframe(df_all)

            progress_bar.progress((i + 1) / len(market_df), text=f"분석 중: {row['종목명']} ({i+1}/{len(market_df)})")
        
        progress_bar.empty()
        st.success("✅ 분석 완료!")

# [수정] 필터링 버튼 클릭 시 동작
# '분석 시작' 버튼을 누르지 않은 상태에서 필터 버튼만 누를 때의 동작
if not start_btn and 'df_all' in st.session_state:
    df = st.session_state['df_all']
    display_df = df.copy()

    if st.session_state.filter == "매수":
        display_df = df[df['상태'].str.contains("매수")]
    elif st.session_state.filter == "매도":
        display_df = df[df['상태'].str.contains("매도")]
    elif st.session_state.filter == "구름대":
        display_df = df[df['일목(일봉)'].str.contains('돌파|이탈|진입')]

    result_title.subheader(f"🔍 결과 리스트 ({st.session_state.filter} / {len(display_df)}개)")
    with main_result_area:
        show_styled_dataframe(display_df)
    
    # Outlook 전송 버튼은 필터링된 결과에 대해서만 표시
    if not display_df.empty:
        email_summary = display_df[['종목명', '현재가', '상태', '일목(일봉)', 'MA 크로스']].to_string(index=False)
        encoded_body = urllib.parse.quote(f"주식 분석 리포트\n\n{email_summary}")
        mailto_url = f"mailto:?subject=주식리포트&body={encoded_body}"
        st.markdown(f'<a href="{mailto_url}" target="_self" style="text-decoration:none;"><div style="background-color:#0078d4;color:white;padding:15px;border-radius:8px;text-align:center;font-weight:bold;">📧 현재 리스트 Outlook 전송</div></a>', unsafe_allow_html=True)

# 최초 실행 시 안내 메시지
elif 'df_all' not in st.session_state:
     with main_result_area:
        st.info("왼쪽 사이드바에서 '분석 시작' 버튼을 눌러주세요.")

