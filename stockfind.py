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

# --- 데이터 수집 및 분석 로직 ---
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
            time.sleep(0.1)
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
        if df is None or len(df) < 40: return None

        # --- CCI 계산 로직 ---
        df['TP'] = (df['고가'] + df['저가'] + df['종가']) / 3
        df['SMA_TP'] = df['TP'].rolling(20).mean()
        mean_dev = df['TP'].rolling(20).apply(lambda x: (x - x.mean()).abs().mean(), raw=True)
        df['CCI'] = (df['TP'] - df['SMA_TP']) / (0.015 * mean_dev)
        df.dropna(inplace=True)

        df['20MA'] = df['종가'].rolling(20).mean()
        ema12 = df['종가'].ewm(span=12, adjust=False).mean()
        ema26 = df['종가'].ewm(span=26, adjust=False).mean()
        df['MACD_hist'] = (ema12 - ema26) - (ema12 - ema26).ewm(span=9, adjust=False).mean()
        df['tr'] = np.maximum(df['고가'] - df['저가'], np.maximum(abs(df['고가'] - df['종가'].shift(1)), abs(df['저가'] - df['종가'].shift(1))))
        df['ATR'] = df['tr'].rolling(14).mean()

        if len(df) < 6: return None
        last, prev = df.iloc[-1], df.iloc[-2]

        price, ma20, macd_last, macd_prev = last['종가'], last['20MA'], last['MACD_hist'], prev['MACD_hist']

        # --- CCI 신호 확인 로직 ---
        cci_window = df.tail(5)
        cci_buy_signal = any(
            ((cci_window['CCI'].shift(1) < threshold) & (cci_window['CCI'] >= threshold)).any()
            for threshold in [-100, 50, 100]
        )
        cci_sell_signal = any(
            ((cci_window['CCI'].shift(1) > threshold) & (cci_window['CCI'] <= threshold)).any()
            for threshold in [100, 50]
        )

        # <<-- MACD 매수/매도 조건 확장 -->>
        macd_buy_condition = (macd_last > 0) or (macd_last > macd_prev and macd_prev < 0)
        macd_sell_condition = (macd_last < 0) or (macd_last < macd_prev and macd_prev > 0)

        diff, disparity = price - ma20, ((price / ma20) - 1) * 100
        disparity_fmt = f"{'+' if disparity > 0 else ''}{round(disparity, 2)}%"
        sl_tp = f"{int(price - last['ATR']*2)} / {int(price + last['ATR']*2)}" if pd.notna(last['ATR']) else "- / -"

        # --- 상태 및 트렌드 판단 로직 (확장된 MACD 조건 적용) ---
        if price > ma20 and macd_last > 0:
            status, trend = ("추가 매수 가능", "🚀 상승세 안정적 (추가 여력)") if 0 <= disparity <= 3 else ("홀드", "📈 상승 추세 유지")
        elif (prev['종가'] < prev['20MA']) and (price > ma20):
            status, trend = "적극 매수", "🔥 엔진 점화"
        elif (abs(price - ma20) / ma20 < 0.03) and macd_buy_condition and cci_buy_signal:
            status, trend = "매수 관심", f"⚓ 반등 준비 {'(MACD 전환)' if macd_last < 0 else ''}(CCI 동시 충족)"
        elif (price < ma20) and macd_sell_condition and cci_sell_signal:
            status, trend = "적극 매도", f"🧊 추세 하락 {'(MACD 전환)' if macd_last > 0 else ''}(CCI 동시 충족)"
        else:
            status, trend = "관망", "🌊 방향 탐색"

        chart_url = f"https://finance.naver.com/item/main.naver?code={code}"
        return [code, name, current_change, int(price), int(ma20), int(diff), disparity_fmt, sl_tp, status, f"{trend} | {'📈 가속' if macd_last > macd_prev else '⚠️ 감속'}", chart_url]

    except Exception as e:
        return None

# --- UI 스타일링 ---
def show_styled_dataframe(dataframe):
    if dataframe is None or dataframe.empty:
        st.info("조건에 맞는 종목이 없거나 분석 전입니다.")
        return

    def color_status(val):
        if any(k in str(val) for k in ['매수', '적극']): return 'color: #ef5350; font-weight: bold'
        if any(k in str(val) for k in ['과열', '주의']): return 'color: #ffa726; font-weight: bold'
        if any(k in str(val) for k in ['매도', '이탈']): return 'color: #42a5f5; font-weight: bold'
        return ''

    st.dataframe(
        dataframe.style.applymap(color_status, subset=['상태'])
        .applymap(lambda x: 'color: #ef5350' if '+' in str(x) else ('color: #42a5f5' if '-' in str(x) else ''), subset=['등락률', '이격률']),
        use_container_width=True,
        column_config={"차트": st.column_config.LinkColumn("차트", display_text="열기"), "코드": st.column_config.TextColumn("코드", width="small")},
        hide_index=True
    )

# --- 메인 UI ---
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
sell_metric.metric("매도/주의", "0개")

# 필터 상태 관리
BUY_STATUS = ["매수", "적극 매수", "추가 매수 가능", "매수 관심"]
SELL_STATUS = ["매도", "적극 매도", "추세 이탈", "과열 주의", "홀드(주의)"]

col1, col2, col3 = st.columns(3)
if 'filter' not in st.session_state: st.session_state.filter = "전체"
if col1.button("🔄 전체 보기", use_container_width=True): st.session_state.filter = "전체"
if col2.button("🔴 매수 관련만", use_container_width=True): st.session_state.filter = "매수"
if col3.button("🔵 매도/주의 관련만", use_container_width=True): st.session_state.filter = "매도"

st.markdown("---")
result_title = st.empty()
result_title.subheader(f"🔍 결과 리스트 ({st.session_state.filter})")
main_result_area = st.empty()

# 1. 분석 시작 버튼 클릭 시
if start_btn:
    market_df = get_market_sum_pages(selected_pages, market)
    if not market_df.empty:
        results = []
        progress_bar = st.progress(0)
        for i, (idx, row) in enumerate(market_df.iterrows()):
            res = analyze_stock(row['종목코드'], row['종목명'], row['등락률'])
            if res:
                results.append(res)
                cols = ['코드', '종목명', '등락률', '현재가', '20MA', '이격률', '상태', '해석', '차트']
                df_all = pd.DataFrame(results, columns=cols)
                st.session_state['df_all'] = df_all # 세션에 저장
                
                # 메트릭 업데이트
                total_metric.metric("전체 종목", f"{len(df_all)}개")
                buy_metric.metric("매수 신호", f"{len(df_all[df_all['상태'].str.contains('|'.join(BUY_STATUS))])}개")
                sell_metric.metric("매도/주의", f"{len(df_all[df_all['상태'].str.contains('|'.join(SELL_STATUS))])}개")
                
                # 실시간 화면 표시
                with main_result_area:
                    show_styled_dataframe(df_all)
            progress_bar.progress((i + 1) / len(market_df))
        st.success("✅ 분석 완료!")

# 2. 분석 완료 후 필터링 적용 (버튼 클릭 대응)
if 'df_all' in st.session_state:
    df = st.session_state['df_all']
    display_df = df.copy()
    if st.session_state.filter == "매수":
        display_df = df[df['상태'].str.contains('|'.join(BUY_STATUS))]
    elif st.session_state.filter == "매도":
        display_df = df[df['상태'].str.contains('|'.join(SELL_STATUS))]
    
    with main_result_area:
        show_styled_dataframe(display_df)

    # Outlook 섹션
    email_summary = display_df[['종목명', '현재가', '상태']].to_string(index=False)
    encoded_body = urllib.parse.quote(f"주식 분석 리포트\n\n{email_summary}")
    mailto_url = f"mailto:?subject=주식리포트&body={encoded_body}"
    st.markdown(f'<a href="{mailto_url}" target="_self" style="text-decoration:none;"><div style="background-color:#0078d4;color:white;padding:15px;border-radius:8px;text-align:center;font-weight:bold;">📧 리스트 Outlook 전송</div></a>', unsafe_allow_html=True)
else:
    with main_result_area:
        st.info("사이드바에서 '분석 시작' 버튼을 눌러주세요.")



