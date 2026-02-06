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

# --- 데이터 수집 함수 (변경 없음) ---
def get_market_sum_pages(page_list, market="KOSPI"):
    # (기존 코드와 동일)
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
    # (기존 코드와 동일)
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

# ★★★ 1. 분석 함수를 새 버전으로 교체 ★★★
def analyze_stock(code, name, current_change):
    try:
        df = get_price_data(code)
        if df is None or len(df) < 40: return None

        # --- 1. 기본 지표 계산 ---
        df['TP'] = (df['고가'] + df['저가'] + df['종가']) / 3
        df['20MA'] = df['종가'].rolling(20).mean()
        
        # MACD
        ema12 = df['종가'].ewm(span=12, adjust=False).mean()
        ema26 = df['종가'].ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        df['MACD_hist'] = macd_line - signal_line

        # CCI
        df['SMA_TP'] = df['TP'].rolling(20).mean()
        mean_dev = df['TP'].rolling(20).apply(lambda x: (x - x.mean()).abs().mean(), raw=True)
        df['CCI'] = (df['TP'] - df['SMA_TP']) / (0.015 * mean_dev)

        # ATR (손절/익절가 계산용)
        df['tr'] = np.maximum(df['고가'] - df['저가'], np.maximum(abs(df['고가'] - df['종가'].shift(1)), abs(df['저가'] - df['종가'].shift(1))))
        df['ATR'] = df['tr'].rolling(14).mean()
        
        df.dropna(inplace=True)
        if len(df) < 2: return None

        last, prev = df.iloc[-1], df.iloc[-2]
        price = last['종가']
        
        # --- 2. 핵심 신호 포착 및 텍스트 변환 ---
        # MACD 신호 해석
        macd_last, macd_prev = last['MACD_hist'], prev['MACD_hist']
        macd_signal = ""
        if macd_last > 0 and macd_prev < 0:
            macd_signal = "MACD 양수 전환"
        elif macd_last < 0 and macd_prev > 0:
            macd_signal = "MACD 음수 전환"
        elif macd_last > 0:
            macd_signal = f"MACD 양수({'가속' if macd_last > macd_prev else '감속'})"
        else:
            macd_signal = f"MACD 음수({'가속' if macd_last < macd_prev else '감속'})"

        # CCI 신호 해석
        cci_last, cci_prev = last['CCI'], prev['CCI']
        cci_signal = ""
        for th in [-100, 0, 50, 100]:
            if cci_prev < th and cci_last >= th:
                cci_signal = f"CCI {th} 상향 돌파"
                break
        if not cci_signal:
            for th in [100, 50, 0, -100]:
                if cci_prev > th and cci_last <= th:
                    cci_signal = f"CCI {th} 하향 돌파"
                    break

        # 이평선(MA) 신호 해석
        ma20 = last['20MA']
        ma_signal = ""
        if price > ma20 and prev['종가'] < prev['20MA']:
            ma_signal = "20일선 상향 돌파"
        elif price < ma20 and prev['종가'] > prev['20MA']:
            ma_signal = "20일선 하향 돌파"
        else:
            disparity = ((price / ma20) - 1) * 100
            ma_signal = f"20일선 {'위' if price > ma20 else '아래'} ({disparity:.1f}%)"

        # --- 3. 최종 판단 및 결과 조합 ---
        # Trend: 포착된 모든 신호를 나열
        trend_signals = [s for s in [ma_signal, macd_signal, cci_signal] if s]
        trend = " | ".join(trend_signals)
        
        # Status: 신호 조합에 따른 최종 의견
        status = "관망"
        if "20일선 상향 돌파" in ma_signal and "양수 전환" in macd_signal:
            status = "🔥 강력 매수"
        elif "20일선 상향 돌파" in ma_signal or ("양수 전환" in macd_signal and "상향 돌파" in cci_signal):
            status = "📈 매수 고려"
        elif "20일선 하향 돌파" in ma_signal and "음수 전환" in macd_signal:
            status = "🚨 강력 매도"
        elif "20일선 하향 돌파" in ma_signal or ("음수 전환" in macd_signal and "하향 돌파" in cci_signal):
            status = "📉 매도 고려"
        elif "20일선 위" in ma_signal and "양수" in macd_signal:
            status = "홀드(상승)"
        elif "20일선 아래" in ma_signal and "음수" in macd_signal:
            status = "관망(하락)"

        # --- 4. 출력 포맷팅 ---
        diff = price - ma20
        disparity_fmt = f"{((price / ma20) - 1) * 100:+.2f}%"
        sl_tp = f"{int(price - last['ATR']*2)} / {int(price + last['ATR']*2)}" if pd.notna(last['ATR']) else "- / -"
        chart_url = f"https://finance.naver.com/item/main.naver?code={code}"

        return [code, name, current_change, int(price), int(ma20), int(diff), disparity_fmt, sl_tp, status, trend, chart_url]

    except Exception as e:
        return None

def show_styled_dataframe(dataframe):
    # (기존 코드와 동일)
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
# UI 부분 (대부분 변경 없음)
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

                # ★★★ 2. 컬럼 이름을 '트렌드 신호'로 변경 ★★★
                df_all = pd.DataFrame(results, columns=['코드', '종목명', '등락률', '현재가', '20MA', '차이', '이격률', '손절/익절', '상태', '트렌드 신호', '차트'])
                st.session_state['df_all'] = df_all
                
                total_metric.metric("전체 종목", f"{len(df_all)}개")
                buy_metric.metric("매수 신호", f"{len(df_all[df_all['상태'].str.contains('매수')])}개")
                sell_metric.metric("매도 신호", f"{len(df_all[df_all['상태'].str.contains('매도')])}개")
                
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

    email_summary = display_df[['종목명', '현재가', '상태', '트렌드 신호']].to_string(index=False)
    encoded_body = urllib.parse.quote(f"주식 분석 리포트\n\n{email_summary}")
    mailto_url = f"mailto:?subject=주식리포트&body={encoded_body}"
    st.markdown(f'<a href="{mailto_url}" target="_self" style="text-decoration:none;"><div style="background-color:#0078d4;color:white;padding:15px;border-radius:8px;text-align:center;font-weight:bold;">📧 리스트 Outlook 전송</div></a>', unsafe_allow_html=True)

else:
    with main_result_area:
        st.info("사이드바에서 '분석 시작' 버튼을 눌러주세요.")






