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
    url = f"https://finance.naver.com/item/fchart.naver?code={code}"
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
        if df is None or len(df) < 60:
            return None

        # ===============================
        # 20일선
        # ===============================
        df['20MA'] = df['종가'].rolling(20).mean()

        # ===============================
        # MACD 계산
        # ===============================
        ema12 = df['종가'].ewm(span=12, adjust=False).mean()
        ema26 = df['종가'].ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()
        df['MACD_hist'] = macd - signal

        # ===============================
        # CCI 계산 (20 period)
        # ===============================
        tp = (df['고가'] + df['저가'] + df['종가']) / 3
        sma_tp = tp.rolling(20).mean()
        mad = tp.rolling(20).apply(lambda x: np.mean(np.abs(x - np.mean(x))), raw=True)
        df['CCI'] = (tp - sma_tp) / (0.015 * mad)

        last = df.iloc[-1]
        price = last['종가']
        ma20 = last['20MA']

        # ===============================
        # 최근 5일 돌파 검사
        # ===============================
        recent = df.iloc[-6:]  # 이전일 포함 6일
        macd_hist = recent['MACD_hist']
        cci_vals = recent['CCI']

        # ----- MACD 상향/하향 돌파 -----
        macd_cross_up = False
        macd_cross_down = False

        for i in range(1, len(macd_hist)):
            if macd_hist.iloc[i-1] < 0 and macd_hist.iloc[i] > 0:
                macd_cross_up = True
            if macd_hist.iloc[i-1] > 0 and macd_hist.iloc[i] < 0:
                macd_cross_down = True

        # ----- CCI 돌파 조건 -----
        cci_cross_up = False
        cci_cross_down = False
        cci_signal_text = ""

        for i in range(1, len(cci_vals)):
            prev = cci_vals.iloc[i-1]
            curr = cci_vals.iloc[i]

            # CCI -100 → 상향 돌파 (= 단기 과매도 해소)
            if prev < -100 and curr > -100:
                cci_cross_up = True
                cci_signal_text = "CCI -100 상향 돌파 (단기 과매도 해소)"

            # CCI 50 → 상향 돌파 (= 초기 모멘텀 진입)
            elif prev < 50 and curr > 50:
                cci_cross_up = True
                cci_signal_text = "CCI 50 상향 돌파 (초기 모멘텀 진입)"

            # CCI 100 → 상향 돌파 (= 강한 상승 모멘텀)"
            elif prev < 100 and curr > 100:
                cci_cross_up = True
                cci_signal_text = "CCI 100 상향 돌파 (강한 상승 모멘텀)"

            # 반대 조건 (매도)
            if prev > 100 and curr < 100:
                cci_cross_down = True
            elif prev > 50 and curr < 50:
                cci_cross_down = True
            elif prev > -100 and curr < -100:
                cci_cross_down = True

        # ===============================
        # 20일선 조건
        # ===============================
        price_condition_up = (price >= ma20) or (abs(price - ma20) / ma20 < 0.03)
        price_condition_down = price < ma20

        # ===============================
        # 최종 조건 (2조건 동시 충족)
        # ===============================
        buy_interest = price_condition_up and (macd_cross_up or cci_cross_up)
        sell_signal = price_condition_down and (macd_cross_down or cci_cross_down)

        # ===============================
        # 상태 결정
        # ===============================
        if price > ma20 and macd_cross_up:
            status = "추가 매수 가능"
            trend = "20일선 상회 + MACD 0선 상향 돌파 (추세 강화)"

        elif buy_interest:
            status = "매수 관심"
            if cci_cross_up:
                trend = cci_signal_text
            else:
                trend = "MACD 0선 상향 돌파 (모멘텀 전환)"

        elif sell_signal:
            status = "적극 매도"
            trend = "20일선 이탈 + 모멘텀 하향 돌파"

        else:
            status = "관망"
            trend = "명확한 복합 신호 없음"

        # ===============================
        # 기타 계산
        # ===============================
        diff = price - ma20
        disparity = ((price / ma20) - 1) * 100
        disparity_fmt = f"{'+' if disparity > 0 else ''}{round(disparity, 2)}%"

        chart_url = f"https://finance.naver.com/item/main.naver?code={code}"

        return [
            code, name, current_change,
            int(price), int(ma20), int(diff),
            disparity_fmt,
            "-",
            status,
            trend,
            chart_url
        ]

    except:
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

