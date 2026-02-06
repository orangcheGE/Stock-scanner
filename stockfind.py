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

# --- 분석 로직 (기능 동일) ---
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

import numpy as np
import pandas as pd

# get_price_data 함수는 이미 구현되어 있다고 가정합니다.
# def get_price_data(code):
#     # ... 주가 데이터를 DataFrame으로 반환하는 로직 ...
#     return df

def analyze_stock(code, name, current_change):
    try:
        df = get_price_data(code)
        if df is None or len(df) < 40: return None

        # --- 보조지표 계산 ---
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

        diff, disparity = price - ma20, ((price / ma20) - 1) * 100
        disparity_fmt = f"{'+' if disparity > 0 else ''}{round(disparity, 2)}%"
        sl_tp = f"{int(price - last['ATR']*2)} / {int(price + last['ATR']*2)}" if pd.notna(last['ATR']) else "- / -"
        
        status = "관망 (신호 대기)" # 기본 상태
        trend = "🌊 횡보 또는 신호 대기"

        # --- 신호 판단 및 상태값 동적 생성 ---
        # 1. '추세 전환 매수' 또는 '눌림목/과열'
        if price > ma20:
            if (prev['종가'] < prev['20MA']):
                status, trend = "20일선 상향 돌파", "🔥 추세 전환 시도"
            elif macd_last > 0 and 0 <= disparity <= 3:
                status, trend = "눌림목 매수 (20일선 지지)", "🚀 상승 중 건강한 조정"
            elif macd_last > 0 and disparity > 3:
                status, trend = "상승 과열 주의", "📈 보유자의 영역"

        # 2. '변곡점 매수' 신호 조합
        cci_window = df.tail(5)
        is_near_ma20 = abs(price - ma20) / ma20 < 0.03
        macd_buy_turn = macd_last > macd_prev and macd_prev < 0

        if is_near_ma20 and (macd_last > 0 or macd_buy_turn):
            reasons = ["20일선 근접"]
            reasons.append("MACD 음수권 전환" if macd_buy_turn else "MACD 양수권")
            
            cci_buy_reasons = []
            if ((cci_window['CCI'].shift(1) < -100) & (cci_window['CCI'] >= -100)).any(): cci_buy_reasons.append("CCI -100 돌파")
            if ((cci_window['CCI'].shift(1) < 50) & (cci_window['CCI'] >= 50)).any(): cci_buy_reasons.append("CCI 50 돌파")
            if ((cci_window['CCI'].shift(1) < 100) & (cci_window['CCI'] >= 100)).any(): cci_buy_reasons.append("CCI 100 돌파")
            
            if cci_buy_reasons:
                reasons.extend(cci_buy_reasons)
                status = " + ".join(reasons)
                trend = "⚓ 바닥 신호 포착"

        # 3. '변곡점 매도' 신호 조합
        macd_sell_turn = macd_last < macd_prev and macd_prev > 0
        if price < ma20 and (macd_last < 0 or macd_sell_turn):
            reasons = ["20일선 이탈"]
            reasons.append("MACD 양수권 전환" if macd_sell_turn else "MACD 음수권")

            cci_sell_reasons = []
            if ((cci_window['CCI'].shift(1) > 100) & (cci_window['CCI'] <= 100)).any(): cci_sell_reasons.append("CCI 100 이탈")
            if ((cci_window['CCI'].shift(1) > 50) & (cci_window['CCI'] <= 50)).any(): cci_sell_reasons.append("CCI 50 이탈")

            if cci_sell_reasons:
                reasons.extend(cci_sell_reasons)
                status = " + ".join(reasons)
                trend = "🧊 고점 신호 포착"
                
        final_trend = f"{trend} | {'📈 가속' if macd_last > macd_prev else '⚠️ 감속'}"
        
        chart_url = f"https://finance.naver.com/item/main.naver?code={code}"
        return [code, name, current_change, int(price), int(ma20), int(diff), disparity_fmt, sl_tp, status, final_trend, chart_url]

    except Exception as e:
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
# UI 부분 (상시 노출 레이아웃)
# -------------------------
st.title("🛡️ 20일선 스마트 데이터 스캐너")

# 사이드바 설정
st.sidebar.header("설정")
market = st.sidebar.radio("시장 선택", ["KOSPI", "KOSDAQ"])
selected_pages = st.sidebar.multiselect("분석 페이지 선택", options=list(range(1, 41)), default=[1])
start_btn = st.sidebar.button("🚀 분석 시작")

# --- 메인 화면: 버튼 및 요약 섹션 (상시 노출) ---
st.subheader("📊 진단 및 필터링")
c1, c2, c3 = st.columns(3)
total_metric = c1.empty()
buy_metric = c2.empty()
sell_metric = c3.empty()

# 기본 메트릭 초기값
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

# 실시간 분석 결과가 나타날 공간
st.markdown("---")
result_title = st.empty()
result_title.subheader(f"🔍 결과 리스트 ({st.session_state.filter})")
main_result_area = st.empty()

# 분석 실행 로직
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
                
                # 메트릭 업데이트
                total_metric.metric("전체 종목", f"{len(df_all)}개")
                buy_metric.metric("매수 신호", f"{len(df_all[df_all['상태'].str.contains('매수')])}개")
                sell_metric.metric("매도 신호", f"{len(df_all[df_all['상태'].str.contains('매도')])}개")
                
                # 실시간 테이블 업데이트
                with main_result_area:
                    show_styled_dataframe(df_all)
            progress_bar.progress((i + 1) / len(market_df))
        st.success("✅ 분석 완료!")

# 분석 후 필터링 적용 출력
if 'df_all' in st.session_state:
    df = st.session_state['df_all']
    display_df = df.copy()
    if st.session_state.filter == "매수": display_df = df[df['상태'].str.contains("매수")]
    elif st.session_state.filter == "매도": display_df = df[df['상태'].str.contains("매도")]
    
    with main_result_area:
        show_styled_dataframe(display_df)

    # Outlook 버튼 상시 노출 (데이터 있을 때만 활성화되는 링크)
    email_summary = display_df[['종목명', '현재가', '상태']].to_string(index=False)
    encoded_body = urllib.parse.quote(f"주식 분석 리포트\n\n{email_summary}")
    mailto_url = f"mailto:?subject=주식리포트&body={encoded_body}"
    st.markdown(f'<a href="{mailto_url}" target="_self" style="text-decoration:none;"><div style="background-color:#0078d4;color:white;padding:15px;border-radius:8px;text-align:center;font-weight:bold;">📧 리스트 Outlook 전송</div></a>', unsafe_allow_html=True)
else:
    with main_result_area:
        st.info("사이드바에서 '분석 시작' 버튼을 눌러주세요.")
