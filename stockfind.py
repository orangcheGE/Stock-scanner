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

# --- 데이터 수집 함수 (안정성 강화) ---
def get_headers():
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://finance.naver.com/'
    }

@st.cache_data(ttl=3600)
def get_market_sum_pages(page_list, market="KOSPI"):
    sosok = 0 if market == "KOSPI" else 1
    codes, names, changes = [], [], []
    for page in page_list:
        url = f"https://finance.naver.com/sise/sise_market_sum.naver?sosok={sosok}&page={page}"
        try:
            res = requests.get(url, headers=get_headers(), timeout=5)
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
            time.sleep(0.2)
        except requests.exceptions.RequestException:
            continue
    return pd.DataFrame({'종목코드': codes, '종목명': names, '등락률': changes})

@st.cache_data(ttl=600)
def get_price_data(code, max_pages=15):
    url = f"https://finance.naver.com/item/sise_day.naver?code={code}"
    dfs = []
    for page in range(1, max_pages+1):
        try:
            res = requests.get(f"{url}&page={page}", headers=get_headers(), timeout=3)
            df_list = pd.read_html(io.StringIO(res.text), encoding='euc-kr')
            if df_list:
                page_df = df_list[0]
                if page_df.empty or pd.isna(page_df.iloc[0,0]): break
                dfs.append(page_df)
        except (pd.errors.ParserError, requests.exceptions.RequestException):
            continue
    if not dfs: return pd.DataFrame()
    df = pd.concat(dfs, ignore_index=True).dropna(how='all')
    df = df.rename(columns=lambda x: x.strip())
    for col in ['종가','고가','저가','거래량']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col].astype(str).str.replace(',', ''), errors='coerce')
    df['날짜'] = pd.to_datetime(df['날짜'], errors='coerce')
    return df.dropna(subset=['날짜','종가']).sort_values('날짜').reset_index(drop=True)

# --- ★★★ '해석' 컬럼을 수정한 최신 분석 함수 ★★★ ---
def analyze_stock(code, name, current_change):
    try:
        df = get_price_data(code)
        if df is None or len(df) < 40: return None
        df['TP'] = (df['고가'] + df['저가'] + df['종가']) / 3
        df['SMA_TP'] = df['TP'].rolling(20).mean()
        mean_dev = df['TP'].rolling(20).apply(lambda x: (x - x.mean()).abs().mean(), raw=True)
        df['CCI'] = (df['TP'] - df['SMA_TP']) / (0.015 * mean_dev + 1e-9)
        df.dropna(subset=['CCI'], inplace=True)
        if len(df) < 20: return None
        df['20MA'] = df['종가'].rolling(20).mean()
        ema12 = df['종가'].ewm(span=12, adjust=False).mean()
        ema26 = df['종가'].ewm(span=26, adjust=False).mean()
        df['MACD_hist'] = (ema12 - ema26) - (ema12 - ema26).ewm(span=9, adjust=False).mean()
        df['tr'] = np.maximum(df['고가'] - df['저가'], np.maximum(abs(df['고가'] - df['종가'].shift(1)), abs(df['저가'] - df['종가'].shift(1))))
        df['ATR'] = df['tr'].rolling(14).mean()
        df.dropna(inplace=True)
        if len(df) < 6: return None
        last, prev = df.iloc[-1], df.iloc[-2]
        price, ma20, macd_last, macd_prev = last['종가'], last['20MA'], last['MACD_hist'], prev['MACD_hist']
        diff, disparity = price - ma20, ((price / ma20) - 1) * 100
        disparity_fmt = f"{'+' if disparity > 0 else ''}{round(disparity, 2)}%"
        sl_tp = f"{int(price - last['ATR']*2)} / {int(price + last['ATR']*2)}" if pd.notna(last['ATR']) else "- / -"
        
        # --- 기본값 설정 ---
        status = "관망 (신호 대기)"
        trend = "뚜렷한 방향성 없는 횡보 구간" # <-- 해석 기본값 변경

        # --- 신호 판단 로직 (해석 부분 수정) ---
        if price > ma20:
            if (prev['종가'] < prev['20MA']):
                status, trend = "20일선 상향 돌파", "단기 추세가 상승으로 전환되는 초기 신호"
            elif macd_last > 0 and 0 <= disparity <= 3:
                status, trend = "눌림목 매수 (20일선 지지)", "상승 추세 중 20일선 지지를 확인하는 매수 기회"
            elif macd_last > 0 and disparity > 3:
                status, trend = "상승 과열 주의", "단기 이격 과다, 추격 매수 위험 구간"
        
        cci_window = df.tail(5)
        is_near_ma20 = abs(price - ma20) / ma20 < 0.03
        macd_buy_turn = macd_last > macd_prev and macd_prev < 0
        if is_near_ma20 and (macd_last > 0 or macd_buy_turn):
            reasons = ["20일선 근접", "MACD 음수권 전환" if macd_buy_turn else "MACD 양수권"]
            cci_buy_reasons = [f"CCI {th} 돌파" for th in [-100, 50, 100] if ((cci_window['CCI'].shift(1) < th) & (cci_window['CCI'] >= th)).any()]
            if cci_buy_reasons:
                reasons.extend(cci_buy_reasons)
                status = " + ".join(reasons)
                trend = "주요 지표들이 동시 바닥 탈출을 암시하는 변곡점"

        macd_sell_turn = macd_last < macd_prev and macd_prev > 0
        if price < ma20 and (macd_last < 0 or macd_sell_turn):
            reasons = ["20일선 이탈", "MACD 양수권 전환" if macd_sell_turn else "MACD 음수권"]
            cci_sell_reasons = [f"CCI {th} 이탈" for th in [100, 50] if ((cci_window['CCI'].shift(1) > th) & (cci_window['CCI'] <= th)).any()]
            if cci_sell_reasons:
                reasons.extend(cci_sell_reasons)
                status = " + ".join(reasons)
                trend = "주요 지표들이 동시 고점 형성 및 하락을 암시"

        final_trend = f"{trend} | {'📈 가속' if macd_last > macd_prev else '⚠️ 감속'}"
        chart_url = f"https://finance.naver.com/item/main.naver?code={code}"
        return [code, name, current_change, int(price), int(ma20), int(diff), disparity_fmt, sl_tp, status, final_trend, chart_url]
    except Exception:
        return None

def show_styled_dataframe(dataframe):
    # ... (UI 함수는 이전과 동일)
    st.dataframe(
        dataframe.style.applymap(lambda v: 'color: #ef5350; font-weight: bold' if any(k in str(v) for k in BUY_KEYWORDS) else ('color: #42a5f5; font-weight: bold' if any(k in str(v) for k in SELL_KEYWORDS) else ('color: #ffa726' if '주의' in str(v) else '')), subset=['상태'])\
                         .applymap(lambda v: 'color: #ef5350' if '+' in str(v) else ('color: #42a5f5' if '-' in str(v) else ''), subset=['등락률', '이격률']),
        use_container_width=True,
        column_config={"차트": st.column_config.LinkColumn("차트", display_text="열기"), "종목코드": st.column_config.TextColumn("코드", width="small")},
        hide_index=True
    )

# --- UI 부분 (이전과 동일) ---
st.title("🛡️ 20일선 스마트 데이터 스캐너")
st.sidebar.header("설정"); market = st.sidebar.radio("시장 선택", ["KOSPI", "KOSDAQ"]); selected_pages = st.sidebar.multiselect("분석 페이지 선택", options=list(range(1, 41)), default=[1])
start_btn = st.sidebar.button("🚀 분석 시작")

st.subheader("📊 진단 및 필터링")
c1, c2, c3 = st.columns(3); total_metric = c1.empty(); buy_metric = c2.empty(); sell_metric = c3.empty()
total_metric.metric("전체 종목", "0개"); buy_metric.metric("매수 신호", "0개"); sell_metric.metric("매도/주의", "0개")

col1, col2, col3 = st.columns(3)
if 'filter' not in st.session_state: st.session_state.filter = "전체"
if col1.button("🔄 전체 보기", use_container_width=True): st.session_state.filter = "전체"
if col2.button("🔴 매수 신호만", use_container_width=True): st.session_state.filter = "매수"
if col3.button("🔵 매도/주의만", use_container_width=True): st.session_state.filter = "매도"
BUY_KEYWORDS = ['돌파', '지지', '근접', '매수', '전환']; SELL_KEYWORDS = ['이탈', '과열', '주의', '하락']

st.markdown("---")
result_title = st.empty(); main_result_area = st.empty(); outlook_area = st.empty()
if 'df_all' not in st.session_state: st.session_state.df_all = pd.DataFrame()

if start_btn:
    st.session_state.filter = "전체"
    market_df = get_market_sum_pages(tuple(selected_pages), market)
    results = []
    progress_bar = st.progress(0)
    result_title.subheader("🔍 분석 중...")
    
    for i, (idx, row) in enumerate(market_df.iterrows()):
        res = analyze_stock(row['종목코드'], row['종목명'], row['등락률'])
        if res:
            results.append(res)
            df_all = pd.DataFrame(results, columns=['종목코드', '종목명', '등락률', '현재가', '20MA', '이격', '이격률', '손절/익절', '상태', '해석', '차트'])
            st.session_state['df_all'] = df_all
            
            buy_count = len(df_all[df_all['상태'].str.contains('|'.join(BUY_KEYWORDS), na=False)])
            sell_count = len(df_all[df_all['상태'].str.contains('|'.join(SELL_KEYWORDS), na=False)])
            total_metric.metric("전체 종목", f"{len(df_all)}개")
            buy_metric.metric("매수 신호", f"{buy_count}개")
            sell_metric.metric("매도/주의", f"{sell_count}개")
            
            with main_result_area.container():
                show_styled_dataframe(df_all)
        progress_bar.progress((i + 1) / len(market_df))
    st.success("✅ 분석 완료!")

df = st.session_state.df_all
if not df.empty:
    display_df = df.copy()
    if st.session_state.filter == "매수":
        display_df = df[df['상태'].str.contains('|'.join(BUY_KEYWORDS), na=False)]
    elif st.session_state.filter == "매도":
        display_df = df[df['상태'].str.contains('|'.join(SELL_KEYWORDS), na=False)]

    result_title.subheader(f"🔍 결과 리스트 ({st.session_state.filter} / {len(display_df)}개)")
    with main_result_area.container():
        show_styled_dataframe(display_df)

    if not display_df.empty:
        email_summary = display_df[['종목명', '현재가', '상태']].to_string(index=False)
        encoded_body = urllib.parse.quote(f"주식 분석 리포트 ({datetime.now().strftime('%Y-%m-%d')})\n\n{email_summary}")
        mailto_url = f"mailto:?subject=주식 리포트&body={encoded_body}"
        outlook_area.markdown(f'<a href="{mailto_url}" target="_self" style="text-decoration:none;"><div style="background-color:#0078d4;color:white;padding:15px;border-radius:8px;text-align:center;font-weight:bold;">📧 현재 리스트 Outlook 전송</div></a>', unsafe_allow_html=True)
else:
    if not start_btn:
      main_result_area.info("사이드바에서 '분석 시작' 버튼을 눌러주세요.")
