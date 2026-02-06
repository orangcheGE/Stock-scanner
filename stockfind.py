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

# --- 데이터 수집 및 분석 로직 (기존과 동일) ---
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
                a_tag = tr.find('a', href=True)
                if not a_tag: continue
                match = re.search(r'code=(\d{6})', a_tag['href'])
                if match:
                    codes.append(match.group(1))
                    names.append(a_tag.get_text(strip=True))
                    changes.append(tds[4].get_text(strip=True))
            time.sleep(0.1)
        except requests.exceptions.RequestException:
            continue
    return pd.DataFrame({'종목코드': codes, '종목명': names, '등락률': changes})

def get_price_data(code, max_pages=15):
    url = f"https://finance.naver.com/item/sise_day.naver?code={code}"
    dfs = []
    for page in range(1, max_pages + 1):
        try:
            res = requests.get(f"{url}&page={page}", headers=get_headers(), timeout=3)
            df_list = pd.read_html(io.StringIO(res.text), encoding='euc-kr')
            if df_list:
                page_df = df_list[0]
                if page_df.empty or page_df.iloc[0,0] is np.nan: break
                dfs.append(page_df)
        except (pd.errors.ParserError, requests.exceptions.RequestException):
            continue
    if not dfs: return pd.DataFrame()
    df = pd.concat(dfs, ignore_index=True).dropna(how='all')
    df = df.rename(columns=lambda x: x.strip())
    for col in ['종가', '고가', '저가', '거래량']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col].astype(str).str.replace(',', ''), errors='coerce')
    df['날짜'] = pd.to_datetime(df['날짜'], errors='coerce')
    return df.dropna(subset=['날짜', '종가']).sort_values('날짜').reset_index(drop=True)

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
        status = "관망 (신호 대기)"
        trend = "🌊 횡보 또는 신호 대기"
        # --- 신호 판단 및 상태값 동적 생성 ---
        if price > ma20:
            if (prev['종가'] < prev['20MA']):
                status, trend = "20일선 상향 돌파", "🔥 추세 전환 시도"
            elif macd_last > 0 and 0 <= disparity <= 3:
                status, trend = "눌림목 매수 (20일선 지지)", "🚀 상승 중 건강한 조정"
            elif macd_last > 0 and disparity > 3:
                status, trend = "상승 과열 주의", "📈 보유자의 영역"
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
        
        # ★★★ 컬럼 순서 수정 ★★★
        return [code, name, current_change, int(price), int(ma20), int(diff), disparity_fmt, sl_tp, status, final_trend, chart_url]
    except Exception as e:
        return None

# --- UI 스타일링 ---
def show_styled_dataframe(dataframe):
    if dataframe is None or dataframe.empty:
        st.info("조건에 맞는 종목이 없거나 분석 전입니다.")
        return
    def color_status(val):
        s_val = str(val)
        if any(k in s_val for k in ['돌파', '지지', '근접', '매수', '전환']): return 'color: #ef5350; font-weight: bold'
        if any(k in s_val for k in ['과열', '주의']): return 'color: #ffa726; font-weight: bold'
        if any(k in s_val for k in ['이탈', '하락']): return 'color: #42a5f5; font-weight: bold'
        return ''
    
    st.dataframe(
        dataframe.style.applymap(color_status, subset=['상태'])
        .applymap(lambda x: 'color: #ef5350' if '+' in str(x) else ('color: #42a5f5' if '-' in str(x) else ''), subset=['등락률', '이격률']),
        use_container_width=True,
        column_config={"차트": st.column_config.LinkColumn("차트", display_text="열기"), "종목코드": st.column_config.TextColumn("코드", width="small")},
        hide_index=True
    )

# --- 메인 UI ---
st.title("🛡️ 20일선 스마트 데이터 스캐너")
st.sidebar.header("설정")
market = st.sidebar.radio("시장 선택", ["KOSPI", "KOSDAQ"])
selected_pages = st.sidebar.multiselect("분석 페이지 선택", options=list(range(1, 41)), default=[1])
if st.sidebar.button("🚀 분석 시작"):
    st.session_state.run_analysis = True
    st.session_state.filter = "전체" # 분석 시작 시 항상 전체 보기로 초기화

# --- 필터 및 메트릭 UI ---
st.subheader("📊 진단 및 필터링")
c1, c2, c3 = st.columns(3)
total_metric = c1.empty()
buy_metric = c2.empty()
sell_metric = c3.empty()

# ★★★ 필터링 키워드 수정 ★★★
BUY_KEYWORDS = ['돌파', '지지', '근접', '매수', '전환']
SELL_KEYWORDS = ['이탈', '과열', '주의', '하락']

col1, col2, col3 = st.columns(3)
if 'filter' not in st.session_state: st.session_state.filter = "전체"
if col1.button("🔄 전체 보기", use_container_width=True): st.session_state.filter = "전체"
if col2.button("🔴 매수 신호만", use_container_width=True): st.session_state.filter = "매수"
if col3.button("🔵 매도/주의만", use_container_width=True): st.session_state.filter = "매도"
st.markdown("---")

result_title = st.empty()
main_result_area = st.empty()

# ★★★ UI 로직 분리 ★★★
# 1. 분석 실행 로직
if st.session_state.get('run_analysis', False):
    market_df = get_market_sum_pages(selected_pages, market)
    if not market_df.empty:
        results = []
        progress_bar = st.progress(0, "종목 분석 중...")
        for i, row in market_df.iterrows():
            res = analyze_stock(row['종목코드'], row['종목명'], row['등락률'])
            if res:
                results.append(res)
            progress_bar.progress((i + 1) / len(market_df))
        
        # ★★★ 컬럼 이름 리스트 수정 ★★★
        cols = ['종목코드', '종목명', '등락률', '현재가', '20MA', '이격', '이격률', '손절/익절', '상태', '해석', '차트']
        st.session_state['df_all'] = pd.DataFrame(results, columns=cols)
        st.success("✅ 분석 완료!")
    else:
        st.error("선택된 페이지에서 종목 정보를 가져오지 못했습니다.")
    st.session_state.run_analysis = False # 분석 완료 후 실행 상태 해제

# 2. 결과 표시 및 필터링 로직 (항상 실행)
if 'df_all' in st.session_state:
    df = st.session_state['df_all']
    display_df = df.copy()

    # 필터링 적용
    if st.session_state.filter == "매수":
        display_df = df[df['상태'].str.contains('|'.join(BUY_KEYWORDS), na=False)]
    elif st.session_state.filter == "매도":
        display_df = df[df['상태'].str.contains('|'.join(SELL_KEYWORDS), na=False)]

    # 메트릭 업데이트
    total_count = len(df)
    buy_count = len(df[df['상태'].str.contains('|'.join(BUY_KEYWORDS), na=False)])
    sell_count = len(df[df['상태'].str.contains('|'.join(SELL_KEYWORDS), na=False)])
    total_metric.metric("전체 종목", f"{total_count}개")
    buy_metric.metric("매수 신호", f"{buy_count}개")
    sell_metric.metric("매도/주의", f"{sell_count}개")
    
    result_title.subheader(f"🔍 결과 리스트 ({st.session_state.filter} / {len(display_df)}개)")
    with main_result_area:
        show_styled_dataframe(display_df)

    # Outlook 섹션 (필터된 결과만 전송)
    if not display_df.empty:
        email_summary = display_df[['종목명', '현재가', '상태']].to_string(index=False)
        encoded_body = urllib.parse.quote(f"주식 분석 리포트 ({datetime.now().strftime('%Y-%m-%d')})\n\n{email_summary}")
        mailto_url = f"mailto:?subject=주식 리포트&body={encoded_body}"
        st.markdown(f'<a href="{mailto_url}" target="_self" style="text-decoration:none;"><div style="background-color:#0078d4;color:white;padding:15px;border-radius:8px;text-align:center;font-weight:bold;">📧 현재 리스트 Outlook 전송</div></a>', unsafe_allow_html=True)
else:
    with main_result_area:
        st.info("사이드바에서 '분석 시작' 버튼을 눌러주세요.")


